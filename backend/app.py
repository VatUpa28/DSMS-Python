from flask import Flask, request, render_template, redirect, url_for
from routes.inventory import inventory_bp
from routes.home import home_bp
from routes.add import add_stone_bp, add_stones_bp
from routes.discount import discount_bp
from routes.upload_rapaport import upload_rapaport_bp
from routes.transactions import transactions_bp
from routes.clients import clients_bp
from routes.contacts import contacts_bp
from routes.shipping import shipping_bp
from routes.barcode import barcode_bp
from routes.api import api_bp
import sqlite3
import csv
import io

app = Flask(__name__, template_folder="../frontend/templates", static_folder="../frontend/static")

app.register_blueprint(home_bp)
app.register_blueprint(inventory_bp)
app.register_blueprint(add_stone_bp)
app.register_blueprint(add_stones_bp)
app.register_blueprint(upload_rapaport_bp)
app.register_blueprint(discount_bp)
app.register_blueprint(clients_bp)
app.register_blueprint(contacts_bp)
app.register_blueprint(shipping_bp)
app.register_blueprint(transactions_bp)
app.register_blueprint(barcode_bp)
app.register_blueprint(api_bp)

if __name__ == "__main__":
    app.run(debug=True)