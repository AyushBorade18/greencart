"""Each file in this folder adds one group of pages to the website."""
from . import public, accounts, store, customer, admin, admin_sales, restock, seller


def register_routes(app):
    for module in (public, accounts, store, customer, admin, admin_sales, restock, seller):
        module.register(app)
