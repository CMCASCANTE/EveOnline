import requests
import json
import time
from datetime import datetime, timedelta

# --- CONSTANTES DE EVE ---
GALLENTE_FW_CORP_ID = 1000037  # Academia Naval Federal (Gallente FW)
JITA_REGION_ID = 10000002       # The Forge (Jita)
DODIXIE_REGION_ID = 10000032    # Sinq Laison (Dodixie)
SALES_TAX_RATE = 0.05           # Tasa de impuesto de venta estimada (5%)
ESI_BASE_URL = "https://esi.evetech.net/latest"
EVEREF_BOM_URL = "https://api.everef.net/type-materials/" 

# --- UTILIDADES DE API ---

def get_historical_average_price_single(type_id, region_id, price_type='sell'):
    """
    Obtiene el precio promedio (AVG Price) de 30 días en una región específica usando la API de History de ESI.
    """
    url = f"{ESI_BASE_URL}/markets/{region_id}/history/?type_id={type_id}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        try:
            data = response.json()
        except json.JSONDecodeError:
            return 0.0, "API Error"
            
        total_price = 0.0
        count = 0
        price_key = 'average' # Usamos el promedio diario para la tendencia.
        
        for day_data in data[::-1]:
             if count >= 30:
                 break
             total_price += day_data.get(price_key, 0.0) 
             count += 1
        
        if count > 0:
            return total_price / count, None
        
        return 0.0, None # No hay datos históricos
        
    except requests.exceptions.RequestException:
        return 0.0, "API Request Failed"

def get_best_historical_price(type_id, price_type='sell'):
    """
    Compara el precio promedio de 30 días entre Jita y Dodixie y devuelve el más alto.
    """
    if price_type == 'sell':
        label = "Venta"
    else:
        label = "Compra"

    # Consultar Jita
    price_jita, error_jita = get_historical_average_price_single(type_id, JITA_REGION_ID, price_type)
    
    # Consultar Dodixie
    price_dodi, error_dodi = get_historical_average_price_single(type_id, DODIXIE_REGION_ID, price_type)
    
    best_price = max(price_jita, price_dodi)
    
    if best_price == price_jita and best_price > 0:
        source = f"Jita {label}"
    elif best_price == price_dodi and best_price > 0:
        source = f"Dodi {label}"
    else:
        source = "N/A"
        
    return best_price, source, (error_jita or error_dodi) # Devolvemos el error si existe

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
    Obtiene el precio de compra actual (Max Buy Price) del material usando EVEMarketer (Jita).
    """
    url = f"https://api.evemarketer.com/ec/marketstat/json?typeid={mat_id}&regionlimit={region_id}"
    try:
        response = requests.get(url, timeout=3)
        
        if response.status_code != 200:
            return 0.0

        try:
            data = response.json()
        except json.JSONDecodeError:
            return 0.0 
            
        if data and data[0].get('buy'):
            return data[0]['buy']['max']
        return 0.0
    except requests.exceptions.RequestException:
        return 0.0

def get_blueprint_materials(bpc_type_id):
    """ Obtiene la Lista de Materiales (BOM) usando EVE Ref. """
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
    # Venta Directa
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
        buy_price = get_current_market_buy_price(required_id, JITA_REGION_ID)
        total_isk_cost += buy_price * quantity

    historical_avg_price, source, _ = get_best_historical_price(type_id, price_type='sell')

    if historical_avg_price == 0.0:
        return None, f"Descartada: {item_name}. Producto final sin datos históricos de venta."

    estimated_revenue = historical_avg_price * (1 - SALES_TAX_RATE)
    isk_profit = estimated_revenue - total_isk_cost
    isk_per_lp = isk_profit / lp_cost
        
    return {
        "Item": item_name,
        "Method": f"Venta Directa ({source.split(' ')[0]})",
        "ISK/LP": isk_per_lp,
        "Profit": isk_profit,
        "LP Cost": lp_cost,
        "Total ISK Cost": total_isk_cost,
        "Avg Sell Price (30D)": historical_avg_price,
    }, None

def calculate_bpc_resale_profit(offer):
    # Reventa de Planos (BPC)
    type_id = offer['type_id']
    lp_cost = offer.get('lp_cost', 0)
    isk_cost = offer.get('isk_cost', 0)
    item_name = get_item_name(type_id)

    if lp_cost == 0:
        return None, f"Descartada: {item_name}. Costo LP es cero."
    
    # Usamos el mejor precio promedio de COMPRA (30D) entre Jita y Dodixie
    bpc_avg_buy_price, source, error_price = get_best_historical_price(type_id, price_type='buy')
    
    if bpc_avg_buy_price == 0.0:
        # Aquí permitimos que el cálculo devuelva un resultado si no hay error de API (sino un 'no hay datos')
        # Pero si no hay datos, la reventa es 0, así que devolvemos None para que se registre el descarte
        if error_price:
             return None, f"BPC Reventa Fallo: {item_name}. Error de API al obtener precio."
        return None, f"BPC Reventa Fallo: {item_name}. Sin datos históricos de compra."
        
    isk_profit = bpc_avg_buy_price - isk_cost
    isk_per_lp = isk_profit / lp_cost
        
    return {
        "Item": item_name,
        "Method": f"Reventa BPC ({source.split(' ')[0]})",
        "ISK/LP": isk_per_lp,
        "Profit": isk_profit,
        "LP Cost": lp_cost,
        "Total ISK Cost": isk_cost,
        "Avg Sell Price (30D)": bpc_avg_buy_price, 
    }, None

def calculate_bpc_manufacturing_profit(offer):
    # Fabricación
    bpc_type_id = offer['type_id']
    lp_cost = offer.get('lp_cost', 0)
    isk_cost_lp = offer.get('isk_cost', 0)
    
    if lp_cost == 0:
        return None, f"Descartada: BPC ID {bpc_type_id}. Costo LP es cero."

    materials, product_type_id = get_blueprint_materials(bpc_type_id)
    
    if not materials or not product_type_id:
        return None, f"BPC Fabricación Fallo: BPC ID {bpc_type_id}. No se pudo obtener el BOM/Producto final."

    material_cost_total = 0.0
    for mat in materials:
        mat_id = mat['type_id']
        quantity = mat['quantity']
        buy_price = get_current_market_buy_price(mat_id, JITA_REGION_ID)
        material_cost_total += buy_price * quantity
    
    total_isk_cost = isk_cost_lp + material_cost_total
    
    product_name = get_item_name(product_type_id)
    
    historical_avg_price, source, _ = get_best_historical_price(product_type_id, price_type='sell')
    
    if historical_avg_price == 0.0:
        return None, f"BPC Fabricación Fallo: BPC {product_name}. Producto final sin datos históricos de venta."

    estimated_revenue = historical_avg_price * (1 - SALES_TAX_RATE)
    isk_profit = estimated_revenue - total_isk_cost
    isk_per_lp = isk_profit / lp_cost
    
    return {
        "Item": product_name,
        "Method": f"Fabricación ({source.split(' ')[0]})",
        "ISK/LP": isk_per_lp,
        "Profit": isk_profit,
        "LP Cost": lp_cost,
        "Total ISK Cost": total_isk_cost,
        "Avg Sell Price (30D)": historical_avg_price,
    }, None

# --- FUNCIÓN PRINCIPAL ---

def main():
    print("--- 💸 Análisis de Tendencia Multi-Mercado + Reventa BPC V7 (Full) 💸 ---")
    print("Nota: Usa el precio promedio de 30 días MÁS ALTO entre Jita y Dodixie. Mayor registro de fallos.")
    
    debug_log = []
    all_results = []
    
    try:
        url_offers = f"{ESI_BASE_URL}/loyalty/stores/{GALLENTE_FW_CORP_ID}/offers/"
        response = requests.get(url_offers)
        response.raise_for_status() 
        offers = response.json()
        
        print(f"Consultando {len(offers)} ofertas de la tienda LP...")
        
        for i, offer in enumerate(offers):
            time.sleep(0.15) 
            
            if 'lp_cost' in offer and offer['lp_cost'] > 0 and ('isk_cost' in offer or offer.get('required_items')):
                
                item_name = get_item_name(offer['type_id'])
                is_blueprint = "Blueprint" in item_name
                
                if is_blueprint:
                    # 1. Reventa de BPC (No depende de BOM)
                    resale_result, error_resale = calculate_bpc_resale_profit(offer)
                    if resale_result:
                        all_results.append(resale_result)
                    else:
                        # Registrar el fallo de Reventa
                        debug_log.append(error_resale)
                    
                    # 2. Fabricación (Depende de BOM)
                    manuf_result, error_manuf = calculate_bpc_manufacturing_profit(offer)
                    if manuf_result:
                        all_results.append(manuf_result)
                    else:
                        # Registrar el fallo de Fabricación
                        debug_log.append(error_manuf)
                        
                else:
                    # Venta Directa
                    direct_result, error = calculate_lp_cost_and_profit(offer)
                    if direct_result:
                        all_results.append(direct_result)
                    elif error:
                        debug_log.append(error)
            
            if (i + 1) % 50 == 0:
                print(f"Procesados {i+1} de {len(offers)} ofertas...")


        if not all_results:
            print("\n❌ **Fallo Crítico:** No se encontró NINGUNA oferta con datos históricos válidos en Jita o Dodixie.")
            print("--- Motivos de descarte (Todos los métodos) ---")
            for log in sorted(list(set(debug_log))):
                print(f"   - {log}")
            return

        # 2. Mostrar Resultados
        all_results.sort(key=lambda x: x['ISK/LP'], reverse=True)
        best_item = all_results[0]
        
        # Filtrar solo resultados positivos para el Top 10 
        positive_results = [r for r in all_results if r['ISK/LP'] > 0]
        
        print(f"\nSe encontraron {len(positive_results)} opciones de rentabilidad positiva con tendencia histórica.")
        print("\n--- 💰 Top 10 TENDENCIA ISK/LP (MÁXIMO entre Jita y Dodixie) 💰 ---")
        print("------------------------------------------------------------------------------------------------------------------------------------------")
        print(f"{'Item (Método / Mercado)':<60} | {'ISK/LP':<8} | {'LP Cost':<8} | {'ISK Cost':<15} | {'Profit Est.':<15} | {'Avg Price (30D)':<15}")
        print("------------------------------------------------------------------------------------------------------------------------------------------")
        for res in positive_results[:10]:
            print(
                f"{res['Item'] + ' (' + res['Method'] + ')':<60} | "
                f"{res['ISK/LP']:,.0f}{' ISK':<4} | "
                f"{res['LP Cost']:,<8} | "
                f"{res['Total ISK Cost']:,<15.0f} | "
                f"{res['Profit']:,<15.0f} | "
                f"{res['Avg Sell Price (30D)']:,<15.2f}"
            )
        print("------------------------------------------------------------------------------------------------------------------------------------------")

        if positive_results:
            print(f"\n✨ **MEJOR TENDENCIA TOTAL:** {best_item['Item']} ({best_item['Method']})")
            print(f"   -> Ratio ISK/LP: **{best_item['ISK/LP']:,.0f}** ISK por LP")

        # --- SECCIÓN DE ERRORES DE BPC PARA DEPURACIÓN ---
        print("\n--- 🐛 DEBUG: DETALLE DE DESCARTES DE BPC 🐛 ---")
        for log in sorted(list(set(debug_log))):
            # Solo mostramos los logs que mencionan 'BPC' o 'Producto final'
            if "BPC" in log or "Producto final" in log or "API Error" in log:
                 print(f"   - {log}")
        
    except requests.exceptions.RequestException as e:
        print(f"\n❌ ERROR FATAL AL CONSULTAR LA API: {e}")
        print("Asegúrate de tener conexión y que la ESI/EVEMarketer/EVE Ref estén activos.")

if __name__ == "__main__":
    main()