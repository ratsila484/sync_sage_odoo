from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
import xmlrpc.client
import pyodbc
from datetime import datetime
import os
import logging
from logging.handlers import RotatingFileHandler
import threading
import time

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Configuration des logs
if not os.path.exists('logs'):
    os.makedirs('logs')
    
log_handler = RotatingFileHandler('logs/sync_app.log', maxBytes=10000000, backupCount=5)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler.setFormatter(log_formatter)
logger = logging.getLogger('sync_app')
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

# Variables globales pour stocker les paramètres de connexion
config = {
    "sage": {
        "server": "MADO-SRV",
        "database": "MADO",
        "trusted": True,
    },
    "odoo": {
        "url": "http://192.168.0.155:8070",
        "db": "mado",
        "user": "rakotooni@gmail.com",
        "pwd": "odoomado25",
    }
}

# État de la synchronisation
sync_status = {
    "running": False,
    "last_run": None,
    "results": [],
    "progress": 0,
    "total": 0
}

def get_sage_connection():
    """Établit une connexion à Sage"""
    try:
        conn_str = (
            f'DRIVER={{ODBC Driver 17 for SQL Server}};'
            f'SERVER={config["sage"]["server"]};'
            f'DATABASE={config["sage"]["database"]};'
        )
        
        if config["sage"]["trusted"]:
            conn_str += 'Trusted_Connection=yes;'
        else:
            conn_str += f'UID={config["sage"]["user"]};PWD={config["sage"]["pwd"]};'
            
        conn_str += 'timeout=30;'
        
        conn = pyodbc.connect(conn_str)
        return conn
    except Exception as e:
        logger.error(f"Erreur de connexion Sage: {str(e)}")
        raise

def get_sage_articles():
    """Récupère les articles et leur stock depuis Sage"""
    try:
        conn = get_sage_connection()
        cursor = conn.cursor()
        
        query = """
        SELECT
            A.AR_Ref AS CodeArticle,
            A.AR_Design AS Article,
            D.DE_Intitule AS Depot,
            S.AS_QteSto AS StockDisponible
        FROM
            F_ARTSTOCK S
            JOIN F_ARTICLE A ON S.AR_Ref = A.AR_Ref
            JOIN F_DEPOT D ON S.DE_No = D.DE_No
        """
        cursor.execute(query)
        articles = cursor.fetchall()
        cursor.close()
        conn.close()
        return articles
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des articles Sage: {str(e)}")
        raise

def connect_to_odoo():
    """Établit une connexion à Odoo"""
    try:
        url = config["odoo"]["url"]
        db = config["odoo"]["db"]
        user = config["odoo"]["user"]
        pwd = config["odoo"]["pwd"]
        
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", allow_none=True)
        uid = common.authenticate(db, user, pwd, {})
        
        if uid == 0:
            raise Exception("Erreur d'authentification Odoo")
        
        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)
        return uid, models
    except Exception as e:
        logger.error(f"Erreur de connexion Odoo: {str(e)}")
        raise

def sync_article(db, uid, pwd, models, article):
    """Synchronise un article spécifique"""
    result = {
        "code": article[0],
        "name": article[1],
        "stock": float(article[3]),
        "status": "pending",
        "message": ""
    }
    
    try:
        # 1. Recherche du produit dans Odoo via le champ 'default_code'
        product_ids = models.execute_kw(db, uid, pwd, 'product.product', 'search', [[['default_code', '=', article[0]]]])
        if not product_ids:
            result["status"] = "error"
            result["message"] = "Produit non trouvé dans Odoo"
            return result
        
        product_id = product_ids[0]
        
        # 2. Recherche de l'emplacement "Stock"
        location_ids = models.execute_kw(db, uid, pwd, 'stock.location', 'search', [[['usage', '=', 'internal']]])
        if not location_ids:
            result["status"] = "error"
            result["message"] = "Aucun emplacement interne trouvé dans Odoo"
            return result
        
        location_id = location_ids[0]
        
        # 3. Recherche de la ligne existante dans stock.quant
        quant_ids = models.execute_kw(db, uid, pwd, 'stock.quant', 'search', [[
            ['product_id', '=', product_id],
            ['location_id', '=', location_id]
        ]])
        
        # Mise à jour ou création
        if quant_ids:
            models.execute_kw(db, uid, pwd, 'stock.quant', 'write', [[quant_ids[0]], {
                'inventory_quantity': result["stock"],
                'quantity': result["stock"],
            }])
            result["status"] = "updated"
            result["message"] = f"Quantité mise à jour: {result['stock']}"
        else:
            models.execute_kw(db, uid, pwd, 'stock.quant', 'create', [{
                'product_id': product_id,
                'location_id': location_id,
                'quantity': result["stock"],
                'inventory_quantity': result["stock"],
            }])
            result["status"] = "created"
            result["message"] = f"Nouvelle quantité créée: {result['stock']}"
        
        # 4. Journalisation (note dans chatter)
        message = f"Mise à jour du stock depuis Sage: {result['stock']} unités."
        models.execute_kw(db, uid, pwd, 'product.product', 'message_post', [product_id], {
            'body': message,
            'message_type': 'comment',
            'subtype_id': 1,
        })
        
        return result
    
    except Exception as e:
        logger.error(f"Erreur lors de la synchronisation de l'article {article[0]}: {str(e)}")
        result["status"] = "error"
        result["message"] = str(e)
        return result

def run_sync_task():
    """Tâche de synchronisation pour l'exécution en arrière-plan"""
    global sync_status
    
    try:
        sync_status["running"] = True
        sync_status["results"] = []
        
        # Récupération des articles Sage
        sage_articles = get_sage_articles()
        sync_status["total"] = len(sage_articles)
        sync_status["progress"] = 0
        
        # Connexion à Odoo
        db = config["odoo"]["db"]
        user = config["odoo"]["user"]
        pwd = config["odoo"]["pwd"]
        uid, models = connect_to_odoo()
        
        # Synchronisation de chaque article
        for article in sage_articles:
            result = sync_article(db, uid, pwd, models, article)
            sync_status["results"].append(result)
            sync_status["progress"] += 1
            time.sleep(0.1)  # Petit délai pour ne pas surcharger Odoo
        
        sync_status["last_run"] = datetime.now()
        
    except Exception as e:
        logger.error(f"Erreur lors de la synchronisation: {str(e)}")
        sync_status["results"].append({"status": "error", "message": str(e)})
    
    finally:
        sync_status["running"] = False




@app.route('/')
def index():
    return render_template('index.html', 
                          config=config, 
                          sync_status=sync_status,
                          current_time=datetime.now())

@app.route('/config', methods=['GET', 'POST'])
def configure():
    if request.method == 'POST':
        # Mise à jour des paramètres Sage
        config["sage"]["server"] = request.form.get('sage_server')
        config["sage"]["database"] = request.form.get('sage_database')
        config["sage"]["trusted"] = request.form.get('sage_trusted') == 'on'
        
        # Mise à jour des paramètres Odoo
        config["odoo"]["url"] = request.form.get('odoo_url')
        config["odoo"]["db"] = request.form.get('odoo_db')
        config["odoo"]["user"] = request.form.get('odoo_user')
        
        # Ne mettre à jour le mot de passe que s'il est fourni
        if request.form.get('odoo_pwd'):
            config["odoo"]["pwd"] = request.form.get('odoo_pwd')
        
        flash('Configuration mise à jour avec succès', 'success')
        return redirect(url_for('index'))
    
    return render_template('config.html', config=config,current_time=datetime.now())

@app.route('/test_connections')
def test_connections():
    results = {"sage": False, "odoo": False, "messages": []}
    
    # Test de la connexion Sage
    try:
        conn = get_sage_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT TOP 1 * FROM F_ARTICLE")
        results["sage"] = True
        results["messages"].append("✅ Connexion à Sage réussie")
        conn.close()
    except Exception as e:
        results["messages"].append(f"❌ Erreur de connexion à Sage: {str(e)}")
    
    # Test de la connexion Odoo
    try:
        uid, models = connect_to_odoo()
        results["odoo"] = True
        results["messages"].append("✅ Connexion à Odoo réussie")
    except Exception as e:
        results["messages"].append(f"❌ Erreur de connexion à Odoo: {str(e)}")
    
    return jsonify(results)

@app.route('/sync', methods=['POST'])
def start_sync():
    if sync_status["running"]:
        return jsonify({"status": "error", "message": "Une synchronisation est déjà en cours"})
    
    # Démarrer la synchronisation dans un thread séparé
    sync_thread = threading.Thread(target=run_sync_task)
    sync_thread.daemon = True
    sync_thread.start()
    
    return jsonify({"status": "started"})

@app.route('/sync_status')
def get_sync_status():
    return jsonify({
        "running": sync_status["running"],
        "progress": sync_status["progress"],
        "total": sync_status["total"],
        "last_run": sync_status["last_run"].strftime('%Y-%m-%d %H:%M:%S') if sync_status["last_run"] else None,
        "results_count": {
            "total": len(sync_status["results"]),
            "success": len([r for r in sync_status["results"] if r["status"] in ["updated", "created"]]),
            "error": len([r for r in sync_status["results"] if r["status"] == "error"])
        }
    })

@app.route('/sync_results')
def get_sync_results():
    return jsonify(sync_status["results"])

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)