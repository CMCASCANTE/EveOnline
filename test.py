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
    # Una oferta puede tener múltiples requisitos ('required_items')
    if 'required_items' not in offer:
        return False # No hay sección de requisitos, asumimos solo LP/ISK (raro)
        
    for item in offer['required_items']:
        # Si el ítem requerido tiene un 'type_id' que no sea ISK (type_id=1), es un material.
        # En las tiendas LP, la única excepción es el ISK base.
        if item.get('type_id') != 58: # Type ID 58 es el ISK en ESI
            return True # Requiere material
            
    # Si solo se requiere ISK, la función anterior es demasiado simple.
    # Vamos al enfoque más simple: si hay MÁS de 1 requisito y el principal es ISK.
    # El ISK base siempre aparece como un ítem requerido en la ESI LP Store.
    
    # Si la lista de requisitos tiene más de un elemento, o tiene un elemento que NO es ISK, requiere material.
    
    # REGLA CLAVE: La ESI siempre lista el ISK como item 58. Si hay más de un ítem, requiere material.
    return len(offer['required_items']) > 1 

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
    print(f"--- 💸 RENTABILIDAD LP+ISK (Federal Defense Union, V15 - ESI Directo) 💸 ---")
    
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
            
        # Filtro de material: Si la función devuelve False, no requiere material.
        if not is_material_required(offer):
            filtered_offers.append(offer)

    if not filtered_offers:
        print("\n⚠️ No se encontraron ofertas que solo requieran LP e ISK base para esta corporación.")
        return

    # 3. Preparar el análisis y la consulta de nombres
    print(f"✅ Ofertas filtradas: {len(filtered_offers)} (Solo LP+ISK, Corp {FDU_CORP_ID})")
    
    type_ids = [offer['type_id'] for offer in filtered_offers]
    item_names = {}
    
    # Obtener nombres de todos los IDs en una sola consulta ESI (muy eficiente)
    try:
        name_response = requests.post(f"{ESI_BASE_URL}/universe/names/?datasource=tranquility", 
                                      json=type_ids, timeout=10)
        name_response.raise_for_status()
        for item in name_response.json():
            item_names[item['id']] = item['name']
    except Exception:
        pass # Ignoramos errores de nombre y usamos el ID

    # 4. Calcular rentabilidad
    results = []
    print(f"--- 📊 Analizando rentabilidad con precio promedio de 30 días... ---")
    
    for offer in filtered_offers:
        type_id = offer['type_id']
        lp_cost = offer['lp_cost']
        quantity = offer['quantity']
        item_name = item_names.get(type_id, f"ID: {type_id}")
        
        # Extraer el costo ISK del required_items (Type ID 58)
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
                "lp_cost": lp_cost,
                "isk_profit": isk_profit,
                "sell_price": avg_price_per_item,
                "quantity": quantity
            })
            
        time.sleep(0.1) 

    # 5. Mostrar Resultados
    results.sort(key=lambda x: x['isk_per_lp'], reverse=True)

    print("\n=======================================================================================")
    print(f"  TOP RENTABILIDAD LP+ISK (Federal Defense Union - Promedio 30 Días)")
    print("=======================================================================================")
    print(f"{'Rentabilidad (ISK/LP)':<25} | {'Ganancia Neta (ISK)':<25} | {'Item (Cantidad)':<40}")
    print("---------------------------------------------------------------------------------------")
    
    for r in results[:10]:
        if r['isk_per_lp'] > 0:
            print(
                f"{r['isk_per_lp']:<25,.0f} | "
                f"{r['isk_profit']:<25,.0f} | "
                f"{r['item_name']} ({r['quantity']})"
            )
            
    print("=======================================================================================")

if __name__ == "__main__":
    main()