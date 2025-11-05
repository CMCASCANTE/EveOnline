import requests
import json
import time
from datetime import datetime, timedelta

# --- CONSTANTES DE EVE ---
ESI_BASE_URL = "https://esi.evetech.net/latest"
JITA_REGION_ID = 10000002
FDU_CORP_ID = 1000181 # Federal Defense Union (ID numérico para la API)

# --- UTILIDADES DE ESI ---

def get_lp_store_offers_esi(corp_id):
    """ Obtiene TODAS las ofertas de la tienda LP de una corporación específica. """
    url = f"{ESI_BASE_URL}/loyalty/stores/{corp_id}/offers/?datasource=tranquility"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json(), None
    except requests.exceptions.RequestException as e:
        return [], f"❌ Error API al obtener ofertas LP: {e}"

def is_material_required(offer):
    """ Filtra ofertas que NO requieren materiales adicionales (solo LP e ISK). """
    # La ESI lista el ISK (Type ID 58) como un requisito. Si hay más de un requisito, o
    # si el único requisito no es ISK, requiere material.
    
    required_items = offer.get('required_items', [])
    
    # Si la lista tiene más de un elemento, definitivamente requiere material además de ISK.
    if len(required_items) > 1:
        return True
        
    # Si la lista tiene exactamente un elemento, y no es ISK, también requiere material.
    if len(required_items) == 1 and required_items[0].get('type_id') != 58:
        return True
        
    # Si no hay requisitos O si solo requiere ISK (o LP), devolvemos False (no requiere material)
    return False

def get_30day_average_price(type_id, region_id=JITA_REGION_ID):
    """ Obtiene el precio de venta promedio de los últimos 30 días. """
    url = f"{ESI_BASE_URL}/markets/{region_id}/history/?datasource=tranquility&type_id={type_id}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status() 
        history_data = response.json()
        
        thirty_days_ago = datetime.now() - timedelta(days=30)
        prices = [day['average'] for day in history_data 
                  if datetime.strptime(day['date'], '%Y-%m-%d') >= thirty_days_ago]
        
        return sum(prices) / len(prices) if prices else 0, None

    except requests.exceptions.RequestException as e:
        return 0, f"Error API: {e}"
    except Exception as e:
        return 0, f"Error de cálculo/formato: {e}"

# --- FUNCIÓN PRINCIPAL ---

def main():
    print(f"--- 💸 RENTABILIDAD LP+ISK DETALLADA (Federal Defense Union, V16) 💸 ---")
    
    # 1. Obtener ofertas de la tienda LP
    all_offers, error = get_lp_store_offers_esi(FDU_CORP_ID)
    
    if error:
        print(f"\n{error}")
        return
        
    # 2. Filtrar ofertas sin materiales
    filtered_offers = []
    for offer in all_offers:
        if 'lp_cost' not in offer or offer['lp_cost'] == 0:
            continue
        if not is_material_required(offer):
            filtered_offers.append(offer)

    if not filtered_offers:
        print("\n⚠️ No se encontraron ofertas que solo requieran LP e ISK base para esta corporación.")
        return

    # 3. Preparar el análisis y la consulta de nombres
    print(f"✅ Ofertas filtradas: {len(filtered_offers)} (Solo LP+ISK, Corp {FDU_CORP_ID})")
    
    type_ids = [offer['type_id'] for offer in filtered_offers]
    item_names = {}
    
    try:
        name_response = requests.post(f"{ESI_BASE_URL}/universe/names/?datasource=tranquility", 
                                      json=type_ids, timeout=10)
        name_response.raise_for_status()
        for item in name_response.json():
            item_names[item['id']] = item['name']
    except Exception:
        pass # Ignorar errores de nombre

    # 4. Calcular rentabilidad
    results = []
    print(f"--- 📊 Analizando rentabilidad con precio promedio de 30 días... ---")
    
    for offer in filtered_offers:
        type_id = offer['type_id']
        lp_cost = offer['lp_cost']
        quantity = offer['quantity']
        item_name = item_names.get(type_id, f"ID: {type_id}")
        
        # Extraer el costo ISK (Type ID 58)
        isk_base_cost = 0
        for req in offer.get('required_items', []):
            if req.get('type_id') == 58: # ISK
                isk_base_cost = req.get('quantity', 0)
                break
        
        avg_price_per_item, price_error = get_30day_average_price(type_id)
        
        if avg_price_per_item > 0:
            total_sell_value = avg_price_per_item * quantity
            total_cost = isk_base_cost
            
            isk_profit = total_sell_value - total_cost
            isk_per_lp = isk_profit / lp_cost if lp_cost > 0 else 0
            
            results.append({
                "item_name": item_name,
                "isk_per_lp": isk_per_lp,
                "isk_profit": isk_profit,
                "lp_cost": lp_cost,
                "isk_base_cost": isk_base_cost,
                "sell_price": avg_price_per_item,
                "quantity": quantity
            })
            
        time.sleep(0.1) 

    # 5. Mostrar Resultados
    results.sort(key=lambda x: x['isk_per_lp'], reverse=True)

    print("\n=====================================================================================================================================================")
    print(f"  TOP RENTABILIDAD LP+ISK (Federal Defense Union - Promedio 30 Días)")
    print("=====================================================================================================================================================")
    print(f"{'Rentabilidad (ISK/LP)':<25} | {'Ganancia Neta (ISK)':<25} | {'Costo LP':<15} | {'Costo ISK':<15} | {'Precio Venta/Item (30D)':<25} | {'Item (Cant.)':<40}")
    print("-----------------------------------------------------------------------------------------------------------------------------------------------------")
    
    for r in results[:25]: # Mostramos los 15 mejores
        if r['isk_per_lp'] > 0:
            print(
                f"{r['isk_per_lp']:<25,.0f} | "
                f"{r['isk_profit']:<25,.0f} | "
                f"{r['lp_cost']:<15,} | "
                f"{r['isk_base_cost']:<15,} | "
                f"{r['sell_price']:<25,.2f} | "
                f"{r['item_name']} ({r['quantity']})"
            )
            
    print("=====================================================================================================================================================")

if __name__ == "__main__":
    main()