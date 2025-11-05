import requests
import json
import time

# --- CONSTANTES DE EVE ---
GALLENTE_FW_CORP_ID = 1000037  # Academia Naval Federal
JITA_REGION_ID = 10000002       # The Forge (Jita)
AMARR_REGION_ID = 10000043      # Sinq Laison (Amarr)
SALES_TAX_RATE = 0.05           # Tasa de impuesto de venta estimada (5%)
ESI_BASE_URL = "https://esi.evetech.net/latest"
EVEREF_BOM_URL = "https://api.everef.net/type-materials/"

# --- UTILIDADES DE API ---

def get_market_price(type_id, price_type='sell'):
    """ 
    Consulta EVEMarketer para el precio en Jita, y si falla, usa Amarr como respaldo.
    Devuelve (precio, region_id_usada)
    """
    regions_to_check = {JITA_REGION_ID: "Jita", AMARR_REGION_ID: "Amarr"}
    
    for region_id, region_name in regions_to_check.items():
        url = f"https://api.evemarketer.com/ec/marketstat/json?typeid={type_id}&regionlimit={region_id}"
        
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            data = response.json()
            
            if data and data[0].get(price_type):
                price = data[0][price_type]['min'] if price_type == 'sell' else data[0][price_type]['max']
                if price > 0.0:
                    return price, region_name
        
        except requests.exceptions.RequestException:
            continue

    return 0.0, None

def get_item_name(type_id):
    """ Obtiene el nombre del ítem usando ESI. """
    url = f"{ESI_BASE_URL}/universe/types/{type_id}/"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 404:
             return f"BPC ID {type_id} (No Name)"
        response.raise_for_status()
        return response.json().get('name', f"Unknown Item ({type_id})")
    except requests.exceptions.RequestException:
        return f"Unknown Item ({type_id})"

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
        buy_price, _ = get_market_price(required_id, price_type='buy')
        
        if buy_price == 0.0:
            mat_name = get_item_name(required_id)
            return None, f"Descartada: {item_name}. Material requerido ({mat_name}) sin precio de compra en Jita/Amarr."
            
        total_isk_cost += buy_price * quantity

    sell_price, _ = get_market_price(type_id, price_type='sell')

    if sell_price == 0.0:
        return None, f"Descartada: {item_name}. Producto final sin precio de venta en Jita/Amarr."

    estimated_revenue = sell_price * (1 - SALES_TAX_RATE)
    isk_profit = estimated_revenue - total_isk_cost
    isk_per_lp = isk_profit / lp_cost
        
    return {
        "Item": item_name,
        "Method": "Venta Directa",
        "ISK/LP": isk_per_lp,
        "Profit": isk_profit,
        "LP Cost": lp_cost,
        "Total ISK Cost": total_isk_cost,
        "Sell Price": sell_price,
    }, None

def calculate_bpc_resale_profit(offer):
    # Lógica de reventa BPC (proxy de contratos)
    type_id = offer['type_id']
    lp_cost = offer.get('lp_cost', 0)
    isk_cost = offer.get('isk_cost', 0)
    item_name = get_item_name(type_id)

    if lp_cost == 0:
        return None, f"Descartada: {item_name}. Costo LP es cero."

    bpc_resale_price, _ = get_market_price(type_id, price_type='buy')
    
    if bpc_resale_price == 0.0:
        return None, f"Descartada: {item_name}. BPC sin precio de compra (proxy) en Jita/Amarr."
        
    isk_profit = bpc_resale_price - isk_cost
    isk_per_lp = isk_profit / lp_cost
        
    return {
        "Item": item_name,
        "Method": "Reventa BPC (Contrato Proxy)",
        "ISK/LP": isk_per_lp,
        "Profit": isk_profit,
        "LP Cost": lp_cost,
        "Total ISK Cost": isk_cost,
        "Sell Price": bpc_resale_price,
    }, None

def calculate_bpc_manufacturing_profit(offer):
    # Lógica de Fabricación (Usando EVE Ref para BOM)
    bpc_type_id = offer['type_id']
    lp_cost = offer.get('lp_cost', 0)
    isk_cost_lp = offer.get('isk_cost', 0)
    
    if lp_cost == 0:
        return None, f"Descartada: BPC ID {bpc_type_id}. Costo LP es cero."

    materials, product_type_id = get_blueprint_materials(bpc_type_id)
    
    if not product_type_id:
        return None, f"Descartada: BPC ID {bpc_type_id}. No se pudo obtener el ID del producto final (Error ESI/Dogma)."

    if not materials:
        product_name = get_item_name(product_type_id)
        return None, f"Descartada: BPC {product_name}. No se encontró la Lista de Materiales (BOM) en EVE Ref."

    material_cost_total = 0.0
    for mat in materials:
        mat_id = mat['type_id']
        quantity = mat['quantity']
        buy_price, _ = get_market_price(mat_id, price_type='buy')
        
        if buy_price == 0.0:
            mat_name = get_item_name(mat_id)
            return None, f"Descartada: BPC ID {bpc_type_id}. Material de fabricación ({mat_name}) sin precio de compra."

        material_cost_total += buy_price * quantity
    
    total_isk_cost = isk_cost_lp + material_cost_total
    
    product_name = get_item_name(product_type_id)
    sell_price, _ = get_market_price(product_type_id, price_type='sell')
    
    if sell_price == 0.0:
        return None, f"Descartada: BPC {product_name}. Producto final sin precio de venta en Jita/Amarr."

    estimated_revenue = sell_price * (1 - SALES_TAX_RATE)
    isk_profit = estimated_revenue - total_isk_cost
    isk_per_lp = isk_profit / lp_cost
    
    return {
        "Item": product_name,
        "Method": "Fabricación",
        "ISK/LP": isk_per_lp,
        "Profit": isk_profit,
        "LP Cost": lp_cost,
        "Total ISK Cost": total_isk_cost,
        "Sell Price": sell_price,
    }, None

# --- FUNCIÓN PRINCIPAL ---

def main():
    print("--- 💸 Análisis de Rentabilidad de Tienda Gallente (Jita/Amarr) - V5 (DEBUG) 💸 ---")
    print("Nota: Se usa Jita como precio primario, Amarr como respaldo. Se ignoran costos de ME/TE/Fees.")
    
    # Lista para almacenar los errores de cada oferta descartada
    debug_log = []
    
    try:
        # 1. Obtener todas las ofertas de la tienda Gallente FW
        url_offers = f"{ESI_BASE_URL}/loyalty/stores/{GALLENTE_FW_CORP_ID}/offers/"
        response = requests.get(url_offers)
        response.raise_for_status() 
        offers = response.json()
        
        all_results = []
        
        for offer in offers:
            time.sleep(0.05) 
            
            if 'lp_cost' in offer and offer['lp_cost'] > 0 and ('isk_cost' in offer or offer.get('required_items')):
                
                item_name = get_item_name(offer['type_id'])
                is_blueprint = "Blueprint" in item_name or "(BPC ID" in item_name
                
                if is_blueprint:
                    # Cálculo 1: Reventa del BPC
                    resale_result, error = calculate_bpc_resale_profit(offer)
                    if resale_result:
                        all_results.append(resale_result)
                    elif error:
                        debug_log.append(error)

                    # Cálculo 2: Fabricación del Producto Final
                    manuf_result, error = calculate_bpc_manufacturing_profit(offer)
                    if manuf_result:
                        all_results.append(manuf_result)
                    elif error:
                        debug_log.append(error)
                        
                else:
                    # Cálculo 3: Venta Directa (Módulos, Ammo, etc.)
                    direct_result, error = calculate_lp_cost_and_profit(offer)
                    if direct_result:
                        all_results.append(direct_result)
                    elif error:
                        debug_log.append(error)
        
        # --- Lógica de Impresión de Resultados ---

        if not all_results:
            print("\n❌ No se encontraron ofertas válidas. Motivos de descarte:")
            for log in debug_log:
                print(f"   - {log}")
            return

        all_results.sort(key=lambda x: x['ISK/LP'], reverse=True)
        best_item = all_results[0]
        
        print(f"\nSe encontraron {len(all_results)} opciones de rentabilidad (incluyendo BPC).")
        print("\n--- 💰 Top 10 Rentabilidad ISK/LP (Venta Directa, Reventa BPC y Fabricación) 💰 ---")
        print("-----------------------------------------------------------------------------------------------------------------------------------")
        print(f"{'Item (Método)':<50} | {'ISK/LP':<8} | {'LP Cost':<8} | {'ISK Total Cost':<15} | {'Profit':<15} | {'Sell Price (Jita/Amarr)':<15}")
        print("-----------------------------------------------------------------------------------------------------------------------------------")
        for res in all_results[:10]:
            print(
                f"{res['Item'] + ' (' + res['Method'] + ')':<50} | "
                f"{res['ISK/LP']:,.0f}{' ISK':<4} | "
                f"{res['LP Cost']:,<8} | "
                f"{res['Total ISK Cost']:,<15.0f} | "
                f"{res['Profit']:,<15.0f} | "
                f"{res['Sell Price']:,<15.2f}"
            )
        print("-----------------------------------------------------------------------------------------------------------------------------------")

        print(f"\n✨ **MEJOR OPCIÓN TOTAL:** {best_item['Item']} ({best_item['Method']})")
        print(f"   -> Ratio ISK/LP: **{best_item['ISK/LP']:,.0f}** ISK por LP")
        
    except requests.exceptions.RequestException as e:
        print(f"\n❌ ERROR FATAL AL CONSULTAR LA API: {e}")
        print("Asegúrate de tener conexión y que la ESI/EVEMarketer/EVE Ref estén activos.")

if __name__ == "__main__":
    main()