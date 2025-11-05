import requests
import json
import time
from datetime import datetime, timedelta

# --- CONSTANTES DE EVE ---
GALLENTE_FW_CORP_ID = 1000037  # Academia Naval Federal (Gallente FW)
JITA_REGION_ID = 10000002       # The Forge (Jita)
SALES_TAX_RATE = 0.05           # Tasa de impuesto de venta estimada (5%)
ESI_BASE_URL = "https://esi.evetech.net/latest"
EVEREF_BOM_URL = "https://api.everef.net/type-materials/" # Para materiales BPC/Fabricación

# --- UTILIDADES DE API ---

def get_historical_average_price(type_id, region_id=JITA_REGION_ID):
    """
    Obtiene el precio de venta promedio (AVG Sell Price) del ítem durante los últimos 30 días 
    utilizando la API de History de ESI.
    """
    url = f"{ESI_BASE_URL}/markets/{region_id}/history/?type_id={type_id}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        total_price = 0.0
        count = 0
        
        # Procesaremos los últimos 30 días
        for day_data in data[::-1]:
             if count >= 30:
                 break
             # Usamos 'average' (promedio diario) para la tendencia
             total_price += day_data.get('average', 0.0) 
             count += 1
        
        if count > 0:
            return total_price / count
        
        return 0.0 # No hay datos históricos
        
    except requests.exceptions.RequestException:
        return 0.0
    except json.JSONDecodeError:
        # Esto sucede si la ESI devuelve un error que no es JSON
        return 0.0

def get_item_name(type_id):
    """ Obtiene el nombre del ítem usando ESI. """
    url = f"{ESI_BASE_URL}/universe/types/{type_id}/"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        if response.status_code == 404:
             return f"BPC ID {type_id} (No Name)"
        return response.json().get('name', f"Unknown Item ({type_id})")
    except requests.exceptions.RequestException:
        return f"Unknown Item ({type_id})"
    except json.JSONDecodeError:
         return f"Unknown Item ({type_id}) (JSON Error)"

def get_current_market_buy_price(mat_id, region_id=JITA_REGION_ID):
    """
    Obtiene el precio de compra actual (Max Buy Price) del material usando EVEMarketer.
    """
    url = f"https://api.evemarketer.com/ec/marketstat/json?typeid={mat_id}&regionlimit={region_id}"
    try:
        response = requests.get(url, timeout=3)
        
        # 1. Verificar el código de estado HTTP
        if response.status_code != 200:
            return 0.0

        # 2. Manejar el error de decodificación de JSON
        try:
            data = response.json()
        except json.JSONDecodeError:
            # Si no es JSON válido (ej. API rechazada/error)
            return 0.0 
            
        if data and data[0].get('buy'):
            return data[0]['buy']['max']
        return 0.0
    except requests.exceptions.RequestException:
        # Fallo de conexión o timeout
        return 0.0

def get_blueprint_materials(bpc_type_id):
    """ Obtiene la Lista de Materiales (BOM) usando EVE Ref. """
    # Se mantiene la función para el cálculo de fabricación
    url_bpc_details = f"{ESI_BASE_URL}/universe/types/{bpc_type_id}/"
    response_details = requests.get(url_bpc_details, timeout=5)
    
    if not response_details.ok:
         return None, None
    try:
        dogma_attributes = response_details.json().get('dogma_attributes', [])
    except json.JSONDecodeError:
        return None, None
    
    product_type_id = next((attr['value'] for attr in dogma_attributes if attr['attribute_id'] == 633), None)
    if not product_type_id:
        return None, None

    url_everef = f"{EVEREF_BOM_URL}{int(product_type_id)}/"
    response = requests.get(url_everef, timeout=10)
    
    if not response.ok:
         return None, None
    try:
        data = response.json()
        materials = data.get('materials', [])
        return materials, int(product_type_id)
    except json.JSONDecodeError:
        return None, None

# --- LÓGICA DE RENTABILIDAD ---

def calculate_lp_cost_and_profit(offer):
    # Lógica de venta directa
    type_id = offer['type_id']
    lp_cost = offer.get('lp_cost', 0)
    isk_cost_base = offer.get('isk_cost', 0)
    required_items = offer.get('required_items', [])
    item_name = get_item_name(type_id)

    if lp_cost == 0:
        return None, f"Descartada: {item_name}. Costo LP es cero."

    total_isk_cost = isk_cost_base
    
    for item in required_items:
        required_id = item['type_id']
        quantity = item['quantity']
        # Usamos precio de compra ACTUAL (corregido) para el costo de adquisición de los materiales
        buy_price = get_current_market_buy_price(required_id)
        
        # Asumimos 0 costo para materiales si no hay precio de compra (tolerancia)
        total_isk_cost += buy_price * quantity

    # *** PUNTO CLAVE: Usamos el precio promedio histórico ***
    historical_avg_price = get_historical_average_price(type_id)

    if historical_avg_price == 0.0:
        return None, f"Descartada: {item_name}. Producto final sin datos históricos de venta."

    estimated_revenue = historical_avg_price * (1 - SALES_TAX_RATE)
    isk_profit = estimated_revenue - total_isk_cost
    isk_per_lp = isk_profit / lp_cost
        
    return {
        "Item": item_name,
        "Method": "Venta Directa",
        "ISK/LP": isk_per_lp,
        "Profit": isk_profit,
        "LP Cost": lp_cost,
        "Total ISK Cost": total_isk_cost,
        "Avg Sell Price (30D)": historical_avg_price,
    }, None

def calculate_bpc_manufacturing_profit(offer):
    # Lógica de Fabricación
    bpc_type_id = offer['type_id']
    lp_cost = offer.get('lp_cost', 0)
    isk_cost_lp = offer.get('isk_cost', 0)
    
    if lp_cost == 0:
        return None, f"Descartada: BPC ID {bpc_type_id}. Costo LP es cero."

    materials, product_type_id = get_blueprint_materials(bpc_type_id)
    
    if not materials or not product_type_id:
        return None, f"Descartada: BPC ID {bpc_type_id}. No se pudo obtener el BOM/Producto final."

    material_cost_total = 0.0
    for mat in materials:
        mat_id = mat['type_id']
        quantity = mat['quantity']
        buy_price = get_current_market_buy_price(mat_id)
        
        # Asumimos 0 costo para materiales de fabricación si no hay precio de compra (tolerancia)
        material_cost_total += buy_price * quantity
    
    total_isk_cost = isk_cost_lp + material_cost_total
    
    product_name = get_item_name(product_type_id)
    
    # *** PUNTO CLAVE: Usamos el precio promedio histórico del producto final ***
    historical_avg_price = get_historical_average_price(product_type_id)
    
    if historical_avg_price == 0.0:
        return None, f"Descartada: BPC {product_name}. Producto final sin datos históricos de venta."

    estimated_revenue = historical_avg_price * (1 - SALES_TAX_RATE)
    isk_profit = estimated_revenue - total_isk_cost
    isk_per_lp = isk_profit / lp_cost
    
    return {
        "Item": product_name,
        "Method": "Fabricación",
        "ISK/LP": isk_per_lp,
        "Profit": isk_profit,
        "LP Cost": lp_cost,
        "Total ISK Cost": total_isk_cost,
        "Avg Sell Price (30D)": historical_avg_price,
    }, None

# --- FUNCIÓN PRINCIPAL ---

def main():
    print("--- 💸 Análisis de Tendencia de Rentabilidad (30 Días) V2 (DEBUG) 💸 ---")
    print("Nota: Usa precio de venta promedio de los últimos 30 días (ESI History) en Jita.")
    print("      Costo de Materiales/Requeridos basado en precio de compra actual (EVEMarketer).")
    
    debug_log = []
    all_results = []
    
    try:
        url_offers = f"{ESI_BASE_URL}/loyalty/stores/{GALLENTE_FW_CORP_ID}/offers/"
        response = requests.get(url_offers)
        response.raise_for_status() 
        offers = response.json()
        
        print(f"Consultando {len(offers)} ofertas de la tienda LP...")
        
        for i, offer in enumerate(offers):
            time.sleep(0.05) 
            
            if 'lp_cost' in offer and offer['lp_cost'] > 0 and ('isk_cost' in offer or offer.get('required_items')):
                
                item_name = get_item_name(offer['type_id'])
                is_blueprint = "Blueprint" in item_name
                
                if is_blueprint:
                    manuf_result, error = calculate_bpc_manufacturing_profit(offer)
                    if manuf_result:
                        all_results.append(manuf_result)
                    elif error:
                        debug_log.append(error)
                        
                else:
                    direct_result, error = calculate_lp_cost_and_profit(offer)
                    if direct_result:
                        all_results.append(direct_result)
                    elif error:
                        debug_log.append(error)
            
            if (i + 1) % 50 == 0:
                print(f"Procesados {i+1} de {len(offers)} ofertas...")


        if not all_results:
            print("\n❌ **Fallo Crítico:** No se encontró NINGUNA oferta con datos históricos válidos.")
            print("--- Motivos de descarte (Filtros Estrictos) ---")
            for log in sorted(list(set(debug_log))):
                print(f"   - {log}")
            return

        # 2. Mostrar Resultados
        all_results.sort(key=lambda x: x['ISK/LP'], reverse=True)
        best_item = all_results[0]
        
        print(f"\nSe encontraron {len(all_results)} opciones de rentabilidad con tendencia histórica.")
        print("\n--- 💰 Top 10 TENDENCIA ISK/LP (Basado en Precio Promedio 30 Días) 💰 ---")
        print("-----------------------------------------------------------------------------------------------------------------------------------")
        print(f"{'Item (Método)':<50} | {'ISK/LP':<8} | {'LP Cost':<8} | {'ISK Cost':<15} | {'Profit Est.':<15} | {'Avg Sell Price (30D)':<15}")
        print("-----------------------------------------------------------------------------------------------------------------------------------")
        for res in all_results[:10]:
            print(
                f"{res['Item'] + ' (' + res['Method'] + ')':<50} | "
                f"{res['ISK/LP']:,.0f}{' ISK':<4} | "
                f"{res['LP Cost']:,<8} | "
                f"{res['Total ISK Cost']:,<15.0f} | "
                f"{res['Profit']:,<15.0f} | "
                f"{res['Avg Sell Price (30D)']:,<15.2f}"
            )
        print("-----------------------------------------------------------------------------------------------------------------------------------")

        print(f"\n✨ **MEJOR TENDENCIA TOTAL:** {best_item['Item']} ({best_item['Method']})")
        print(f"   -> Ratio ISK/LP: **{best_item['ISK/LP']:,.0f}** ISK por LP")
        
    except requests.exceptions.RequestException as e:
        print(f"\n❌ ERROR FATAL AL CONSULTAR LA API: {e}")
        print("Asegúrate de tener conexión y que la ESI/EVEMarketer/EVE Ref estén activos.")

if __name__ == "__main__":
    main()