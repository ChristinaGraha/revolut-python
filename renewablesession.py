import requests
import xmlrpc.client
import os
from revolut.session import RenewableSession
from dotenv import load_dotenv
import json
import difflib

# Load environment variables from your .env file
load_dotenv(dotenv_path='/Users/christina/odoo/.env')

# Load required tokens and client information from environment variables
refresh_token = os.getenv('REVOLUT_REFRESH_TOKEN')
client_id = os.getenv('REVOLUT_CLIENT_ID')
jwt = os.getenv('REVOLUT_JWT')

# Odoo connection details from environment variables
odoo_url = os.getenv('ODOO_URL')
odoo_db = os.getenv('ODOO_DB')
odoo_username = os.getenv('ODOO_USERNAME')
odoo_password = os.getenv('ODOO_PASSWORD')

# Connect to Odoo
common = xmlrpc.client.ServerProxy(f'{odoo_url}/xmlrpc/2/common')
uid = common.authenticate(odoo_db, odoo_username, odoo_password, {})
models = xmlrpc.client.ServerProxy(f'{odoo_url}/xmlrpc/2/object')

# Function to search for possible vendor matches
def find_similar_vendors(odoo, merchant_name):
    # Fetch all vendors from Odoo
    vendors = odoo.execute_kw(odoo_db, uid, odoo_password, 'res.partner', 'search_read', 
                              [[['supplier_rank', '>', 0]]], {'fields': ['name']})
    vendor_names = [vendor['name'] for vendor in vendors]
    
    # Find the closest matches using difflib
    close_matches = difflib.get_close_matches(merchant_name, vendor_names, n=5, cutoff=0.5)
    
    return close_matches

# Get vendor from user selection
def get_vendor_by_selection(odoo, close_matches):
    if not close_matches:
        return None

    print("Possible vendor matches:")
    for idx, vendor_name in enumerate(close_matches):
        print(f"{idx + 1}. {vendor_name}")

    try:
        selection = int(input("Select the vendor by number (or press 0 to skip): "))
        if selection == 0:
            return None
        else:
            selected_vendor_name = close_matches[selection - 1]
            vendor = odoo.execute_kw(odoo_db, uid, odoo_password, 'res.partner', 'search', 
                                     [[['name', '=', selected_vendor_name]]])
            return vendor[0] if vendor else None
    except (ValueError, IndexError):
        print("Invalid selection.")
        return None

# Create a vendor bill in Odoo
def create_vendor_bill(odoo, vendor_id, amount, currency, transaction_description):
    if vendor_id:
        print(f"Creating bill with amount: {amount} {currency}")  # Debugging line
        bill_data = {
            'partner_id': vendor_id,
            'move_type': 'in_invoice',  # Vendor bill
            'currency_id': odoo.execute_kw(odoo_db, uid, odoo_password, 'res.currency', 'search', [[['name', '=', currency]]])[0],  # Find currency
            'invoice_line_ids': [(0, 0, {
                'name': transaction_description,  # Description of bill
                'quantity': 1,  # 1 unit
                'price_unit': abs(amount)  # Amount for the bill
            })]
        }
        
        bill_id = odoo.execute_kw(odoo_db, uid, odoo_password, 'account.move', 'create', [bill_data])
        print(f"Vendor bill created for vendor ID {vendor_id} with bill ID: {bill_id}.")
    else:
        print("Vendor not provided, skipping bill creation.")

# Create a renewable session using refresh token, client_id, and JWT
try:
    print("Creating session...")
    session = RenewableSession(
        refresh_token=refresh_token,
        client_id=client_id,
        jwt=jwt
    )
    print("Session created successfully.")
    
    # Use the session to fetch the latest access token
    access_token = session.access_token
    print(f"Access Token: {access_token}")
    
    # Make an API request using the access token
    url = "https://b2b.revolut.com/api/1.0/transactions"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    
    response = requests.get(url, headers=headers)
    
    # Handle and print the response
    print(f"Response Status Code: {response.status_code}")
    
    if response.status_code == 200:
        transactions = response.json()
        
        # Filter transactions for type "card_payment" and state "completed"
        filtered_transactions = [
            tx for tx in transactions 
            if tx.get('type') == 'card_payment' and tx.get('state') == 'completed'
        ]
        
        # Limit to 10 transactions and pretty-print them
        limited_transactions = filtered_transactions[-10:]
        for transaction in limited_transactions:
            print(json.dumps(transaction, indent=4))  # Pretty print the transaction
            print("#" * 30)  # Print a line of ####### to separate transactions

        # Ask if you want to import them into Odoo
        for transaction in limited_transactions:
            merchant_name = transaction['merchant']['name']
            legs = transaction.get('legs', [])
            
            if legs:
                bill_amount = legs[0].get('bill_amount')
                amount = legs[0]['amount']
                currency = legs[0]['currency']
                bill_currency = legs[0].get('bill_currency')

                if bill_amount and bill_currency:
                    amount_to_use = bill_amount
                    currency_to_use = bill_currency
                else:
                    amount_to_use = amount
                    currency_to_use = currency

                description = legs[0]['description']

                print(f"\nProcessing transaction with merchant: {merchant_name}")
                print(f"Amount: {amount_to_use} {currency_to_use}")
                print(f"Description: {description}")
                
                # Ask if you want to import this transaction into Odoo
                import_to_odoo = input(f"Would you like to import the transaction for '{merchant_name}' into Odoo? (yes/no): ").strip().lower()
                if import_to_odoo == 'yes':
                    # Find similar vendors using fuzzy matching
                    close_matches = find_similar_vendors(models, merchant_name)
                    
                    if close_matches:
                        vendor_id = get_vendor_by_selection(models, close_matches)
                        
                        if vendor_id:
                            create_vendor_bill(models, vendor_id, amount_to_use, currency_to_use, description)
                        else:
                            print(f"Skipping vendor creation for '{merchant_name}'.")
                    else:
                        print(f"No similar vendors found for '{merchant_name}'.")
                else:
                    print(f"Skipping transaction for merchant '{merchant_name}'.")
            else:
                print(f"Skipping transaction for merchant '{merchant_name}' due to missing transaction details.")
    else:
        print(f"Error fetching transactions: {response.status_code}")
except Exception as e:
    print(f"An error occurred: {e}")
