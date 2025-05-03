from flask import Flask, render_template, request, jsonify, flash, redirect, url_for, send_from_directory
from datetime import datetime
import os
import json
import xmlrpc.client
from openpyxl import Workbook
import threading

app = Flask(__name__)
app.secret_key = "sage_odoo_sync_app_secret_key"

# Configuration par défaut
DEFAULT_CONFIG = {
    "odoo": {
        "url": "http://192.168.0.155:8070",
        "db": "mado",
        "username": "rakotooni@gmail.com",
        "password": "odoomado25"
    }
}

# État de la synchronisation
sync_status = {
    "running": False,
    "last_run": None,
    "progress": 0,
    "total": 100,
    "results": [],
    "results_count": {"total": 0, "created": 0, "updated": 0, "error": 0}
}

# Historique des exports
export_history = []

# Dossier pour les fichiers d'export
EXPORT_FOLDER = 'exports'
if not os.path.exists(EXPORT_FOLDER):
    os.makedirs(EXPORT_FOLDER)

# Charger la configuration
def load_config():
    if os.path.exists('config.json'):
        with open('config.json', 'r') as f:
            return json.load(f)
    return DEFAULT_CONFIG

# Sauvegarder la configuration
def save_config(config):
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=4)

# Routes principales
@app.route('/')
def index():
    return render_template('index.html', 
                          sync_status=sync_status, 
                          current_time=datetime.now())

@app.route('/config')
def config():
    config_data = load_config()
    return render_template('config.html', 
                          config=config_data, 
                          current_time=datetime.now())

@app.route('/export')
def export():
    config_data = load_config()
    return render_template('export.html', 
                          odoo_config=config_data.get('odoo'), 
                          export_history=export_history,
                          current_time=datetime.now())

# Route pour les tests de connexion
@app.route('/test_connections')
def test_connections():
    config = load_config()
    results = {"sage": False, "odoo": False, "messages": []}
    
    # Test de connexion Odoo
    try:
        url = config["odoo"]["url"]
        db = config["odoo"]["db"]
        username = config["odoo"]["username"]
        password = config["odoo"]["password"]
        
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        uid = common.authenticate(db, username, password, {})
        
        if uid:
            results["odoo"] = True
            results["messages"].append(f"Connexion à Odoo réussie (UID: {uid})")
        else:
            results["messages"].append("Échec d'authentification Odoo")
    except Exception as e:
        results["messages"].append(f"Erreur de connexion Odoo: {str(e)}")
    
    # Test de connexion Sage (simulé pour cet exemple)
    results["sage"] = True
    results["messages"].append("Connexion à Sage réussie (simulation)")
    
    return jsonify(results)

# Route pour sauvegarder la configuration Odoo
@app.route('/save_odoo_config', methods=['POST'])
def save_odoo_config():
    config = load_config()
    
    config["odoo"] = {
        "url": request.form.get('odoo_url'),
        "db": request.form.get('odoo_db'),
        "username": request.form.get('odoo_username'),
        "password": request.form.get('odoo_password') or config["odoo"].get("password", "")
    }
    
    save_config(config)
    flash("Configuration Odoo enregistrée avec succès", "success")
    return redirect(url_for('export'))

# Route pour obtenir le statut de synchronisation
@app.route('/sync_status')
def get_sync_status():
    return jsonify(
        running=sync_status["running"],
        progress=sync_status["progress"],
        total=sync_status["total"],
        results_count=sync_status["results_count"],
        last_run=sync_status["last_run"].strftime('%d/%m/%Y %H:%M:%S') if sync_status["last_run"] else None
    )

# Route pour obtenir les résultats de synchronisation
@app.route('/sync_results')
def get_sync_results():
    return jsonify(sync_status["results"])

# Route pour démarrer la synchronisation
@app.route('/sync', methods=['POST'])
def start_sync():
    if sync_status["running"]:
        return jsonify({"status": "error", "message": "Une synchronisation est déjà en cours"})
    
    # Réinitialiser le statut
    sync_status["running"] = True
    sync_status["progress"] = 0
    sync_status["results"] = []
    sync_status["results_count"] = {"total": 0, "created": 0, "updated": 0, "error": 0}
    
    # Démarrer la synchronisation dans un thread séparé
    thread = threading.Thread(target=run_sync)
    thread.daemon = True
    thread.start()
    
    return jsonify({"status": "started"})

def run_sync():
    # Simuler une synchronisation
    import time
    import random
    
    try:
        total_items = random.randint(30, 50)
        sync_status["total"] = total_items
        
        for i in range(total_items):
            # Simuler le traitement d'un article
            time.sleep(0.2)
            
            status = random.choice(["created", "updated", "error"])
            result = {
                "code": f"PROD{1000+i}",
                "name": f"Article Test {i+1}",
                "stock": random.randint(0, 100),
                "status": status,
                "message": "Opération réussie" if status != "error" else "Erreur de connexion"
            }
            
            sync_status["results"].append(result)
            sync_status["results_count"]["total"] += 1
            sync_status["results_count"][status] += 1
            sync_status["progress"] = i + 1
    except Exception as e:
        print(f"Erreur lors de la synchronisation: {str(e)}")
    finally:
        sync_status["running"] = False
        sync_status["last_run"] = datetime.now()

# Fonction pour exporter les factures
def export_invoices(all_invoices=True, date_from=None, date_to=None):
    config = load_config()
    odoo_config = config.get("odoo", DEFAULT_CONFIG["odoo"])
    
    url = odoo_config["url"]
    db = odoo_config["db"]
    username = odoo_config["username"]
    password = odoo_config["password"]
    
    try:
        # Connexion à Odoo
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        uid = common.authenticate(db, username, password, {})
        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
        
        # Construire le domaine de recherche
        domain = [['move_type', '=', 'out_invoice'], ['state', '!=', 'cancel']]
        
        if not all_invoices and date_from and date_to:
            domain.append(['invoice_date', '>=', date_from])
            domain.append(['invoice_date', '<=', date_to])
        
        # Récupérer toutes les factures clients
        invoice_ids = models.execute_kw(db, uid, password,
            'account.move', 'search', [domain])
        
        invoices = models.execute_kw(db, uid, password,
            'account.move', 'read',
            [invoice_ids],
            {'fields': ['name', 'invoice_date', 'partner_id', 'user_id', 'ref', 'invoice_line_ids']})
        
        # Créer fichier Excel
        wb = Workbook()
        ws = wb.active
        ws.title = "Articles Factures"
        
        # En-têtes
        ws.append([
            "N° Facture", "Référence", "Client", "Date",
            "Responsable", "Nom Article", "Quantité", "Prix Unitaire", "Total Ligne"
        ])
        
        # Traitement de chaque facture
        for invoice in invoices:
            facture_num = invoice['name']
            date_facture = invoice['invoice_date'] or ""
            client = invoice['partner_id'][1] if invoice['partner_id'] else "Sans client"
            responsable = invoice['user_id'][1] if invoice['user_id'] else "Non assigné"
            reference = invoice['ref'] or "Aucune"
            line_ids = invoice['invoice_line_ids']
            
            if not line_ids:
                continue
            
            lines = models.execute_kw(db, uid, password,
                'account.move.line', 'read',
                [line_ids],
                {'fields': ['product_id', 'quantity', 'price_unit', 'name']})
            
            for line in lines:
                nom_article = line['product_id'][1] if line['product_id'] else line['name']
                quantite = line['quantity']
                prix = line['price_unit']
                total = quantite * prix
                
                ws.append([
                    facture_num,
                    reference,
                    client,
                    date_facture,
                    responsable,
                    nom_article,
                    quantite,
                    prix,
                    total
                ])
        
        # Générer un nom de fichier avec timestamp
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"factures_articles_{timestamp}.xlsx"
        filepath = os.path.join(EXPORT_FOLDER, filename)
        
        # Sauvegarder le fichier Excel
        wb.save(filepath)
        
        # Ajouter à l'historique des exports
        export_entry = {
            "date": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "filename": filename,
            "path": filepath
        }
        export_history.insert(0, export_entry)
        
        # Garder seulement les 10 derniers exports dans l'historique
        if len(export_history) > 10:
            export_history.pop()
        
        return {"status": "success", "filename": filename}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}

# Route pour exporter les factures
@app.route('/export_invoices', methods=['POST'])
def handle_export_invoices():
    if request.content_type == 'application/json':
        data = request.json
        all_invoices = data.get('all_invoices', True)
        date_from = data.get('date_from')
        date_to = data.get('date_to')
    else:
        all_invoices = 'all_invoices' in request.form
        date_from = request.form.get('date_from')
        date_to = request.form.get('date_to')
    
    result = export_invoices(all_invoices, date_from, date_to)
    
    if request.content_type == 'application/json':
        return jsonify(result)
    else:
        if result["status"] == "success":
            flash(f"Export réussi! Le fichier '{result['filename']}' a été créé.", "success")
        else:
            flash(f"Erreur lors de l'export: {result['message']}", "danger")
        return redirect(url_for('export'))

# Route pour télécharger un fichier d'export
@app.route('/download_export/<filename>')
def download_export(filename):
    return send_from_directory(EXPORT_FOLDER, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)