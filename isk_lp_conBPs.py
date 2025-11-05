import requests

# --- CONSTANTES DE EVE ---
GALLENTE_FW_CORP_ID = 1000037  # Academia Naval Federal
JITA_REGION_ID = 10000002       # The Forge
SALES_TAX_RATE = 0.05           # Tasa de impuesto de venta estimada (5%)
ESI_BASE_URL = "https://esi.evetech.net/latest"

# API de Terceros para la BOM (Lista de Materiales)
FUZZWORK_BOM_URL = "https://www.fuzzwork.co.uk/blueprint/api/"

# --- UTILIDADES DE API ---

def get_market_price(type_id, region_id, price_type='sell'):
    """ Consulta EVEMarketer para el precio (sell min o buy max). """
    url = f"https://api.evemarketer.com/ec/marketstat/json?typeid={type_id}&regionlimit={region_id}"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data and data[0].get(price_type):
            return data[0][price_type]['min'] if price_type == 'sell' else data[0][price_type]['max']
        return 0.0
    except requests.exceptions.RequestException:
        return 0.0

def get_item_name(type_id):
    """ Obtiene el nombre del ítem usando ESI. """
    url = f"{ESI_BASE_URL}/universe/types/{type_id}/"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return response.json().get('name', f"Unknown Item ({type_id})")
    except requests.exceptions.RequestException:
        return f"Unknown Item ({type_id})"

def get_blueprint_materials(bpc_type_id):
    """
    Obtiene la Lista de Materiales (BOM) para la fabricación del producto
    a partir del BPC utilizando la API de Fuzzwork.
    """
    # La API de Fuzzwork acepta el Type ID del PRODUCTO final, no el del BPC.
    # Necesitamos el Type ID del producto que se crea a partir de este BPC.
    # Asumiremos que el producto final tiene un Type ID que es un número consecutivo (esto es una simplificación).
    # Sin embargo, la ESI proporciona el ID del producto final en el endpoint del BPC.
    
    # Dado que estamos procesando la oferta de LP, el Type ID de la oferta es el BPC ID.
    # Usaremos el endpoint /universe/types/{type_id} para obtener el 'product_type_id'
    
    url_bpc_details = f"{ESI_BASE_URL}/universe/types/{bpc_type_id}/"
    response_details = requests.get(url_bpc_details, timeout=5)
    if not response_details.ok:
        return None, None

    # El 'product_type_id' se encuentra dentro de la sección 'dogma_attributes' o 'market_group_id',
    # pero es más fiable consultarlo en una base de datos externa como Fuzzwork.

    # Consulta a la API de Fuzzwork (usando el Type ID del BPC) para obtener la BOM:
    # Fuzzwork es inteligente y puede resolver el BPC ID para dar la BOM del producto final.
    url_fuzzwork = f"{FUZZWORK_BOM_URL}?typeid={bpc_type_id}&me=0&runs=1" # ME=0 y 1 run base
    response = requests.get(url_fuzzwork, timeout=10)
    response.raise_for_status()

    # La API de Fuzzwork devuelve el resultado dentro de la clave 'materials'
    data = response.json()
    if data and data.get('materials'):
        product_id = data.get('productTypeID')
        return data['materials'], product_id
    
    return None, None

# --- LÓGICA DE RENTABILIDAD ---

def calculate_lp_cost_and_profit(offer):
    """
    Calcula el costo total en ISK y la rentabilidad ISK/LP (Venta Directa).
    """
    # Lógica idéntica al script anterior (ya incluye el costo de materiales de la tienda)
    type_id = offer['type_id']
    lp_cost = offer.get('lp_cost', 0)
    isk_cost_base = offer.get('isk_cost', 0)
    required_items = offer.get('required_items', [])
    
    total_isk_cost = isk_cost_base
    
    for item in required_items:
        required_id = item['type_id']
        quantity = item['quantity']
        buy_price = get_market_price(required_id, JITA_REGION_ID, price_type='buy')
        if buy_price == 0.0:
            return None # Material requerido sin precio de compra
        total_isk_cost += buy_price * quantity

    item_name = get_item_name(type_id)
    sell_price = get_market_price(type_id, JITA_REGION_ID, price_type='sell')

    if sell_price == 0.0 or lp_cost == 0:
        return None

    estimated_revenue = sell_price * (1 - SALES_TAX_RATE)
    isk_profit = estimated_revenue - total_isk_cost
    isk_per_lp = isk_profit / lp_cost if isk_profit > 0 else 0.0
        
    return {
        "Item": item_name,
        "Method": "Venta Directa",
        "ISK/LP": isk_per_lp,
        "Profit": isk_profit,
        "LP Cost": lp_cost,
        "Total ISK Cost": total_isk_cost,
        "Sell Price": sell_price,
    }

def calculate_bpc_resale_profit(offer):
    """
    Calcula la rentabilidad de reventa del BPC (proxy de contratos).
    """
    type_id = offer['type_id']
    lp_cost = offer.get('lp_cost', 0)
    isk_cost = offer.get('isk_cost', 0)
    item_name = get_item_name(type_id)

    # Proxy: Precio de compra más alto (Max Buy) de Jita.
    bpc_resale_price = get_market_price(type_id, JITA_REGION_ID, price_type='buy')
    
    if bpc_resale_price == 0.0 or lp_cost == 0:
        return None
        
    isk_profit = bpc_resale_price - isk_cost
    isk_per_lp = isk_profit / lp_cost if isk_profit > 0 else 0.0
        
    return {
        "Item": item_name,
        "Method": "Reventa BPC (Contrato Proxy)",
        "ISK/LP": isk_per_lp,
        "Profit": isk_profit,
        "LP Cost": lp_cost,
        "Total ISK Cost": isk_cost,
        "Sell Price": bpc_resale_price,
    }

def calculate_bpc_manufacturing_profit(offer):
    """
    Calcula la rentabilidad de fabricación del producto final (totalmente automatizado).
    """
    bpc_type_id = offer['type_id']
    lp_cost = offer.get('lp_cost', 0)
    isk_cost_lp = offer.get('isk_cost', 0)
    
    # 1. Obtener la Lista de Materiales (BOM) y el ID del producto final
    materials, product_type_id = get_blueprint_materials(bpc_type_id)

    if not materials or not product_type_id or lp_cost == 0:
        return None

    # 2. Calcular el costo total de los materiales
    material_cost_total = 0.0
    for mat in materials:
        mat_id = mat['typeID']
        quantity = mat['quantity']
        # El costo de los materiales se basa en el precio de COMPRA (Max Buy) en Jita.
        buy_price = get_market_price(mat_id, JITA_REGION_ID, price_type='buy')
        
        if buy_price == 0.0:
            # Si un material no tiene precio de compra, asumimos que no es viable
            # print(f"   -> Material {get_item_name(mat_id)} sin precio de compra. Fabricación no calculable.")
            return None 

        material_cost_total += buy_price * quantity
    
    # 3. Costos Adicionales (Ignoramos fees de planta/impuestos para una simplificación)
    # Costo Total = Costo del BPC (ISK) + Costo de Materiales
    total_isk_cost = isk_cost_lp + material_cost_total
    
    # 4. Precio de Venta del Producto Final
    product_name = get_item_name(product_type_id)
    sell_price = get_market_price(product_type_id, JITA_REGION_ID, price_type='sell')
    
    if sell_price == 0.0:
        return None

    # 5. Calcular Ganancia y ISK/LP
    # Nota: Asumimos 1 run de BPC, por lo que el precio de venta es por 1 unidad
    estimated_revenue = sell_price * (1 - SALES_TAX_RATE)
    isk_profit = estimated_revenue - total_isk_cost
    isk_per_lp = isk_profit / lp_cost if isk_profit > 0 else 0.0

    return {
        "Item": product_name + " (Fabrica)",
        "Method": "Fabricación (BOM + Materiales)",
        "ISK/LP": isk_per_lp,
        "Profit": isk_profit,
        "LP Cost": lp_cost,
        "Total ISK Cost": total_isk_cost,
        "Sell Price": sell_price,
        "BPC ID": bpc_type_id
    }

# --- FUNCIÓN PRINCIPAL ---

def main():
    print("--- 💸 Análisis de Rentabilidad de Tienda Gallente (Jita) - AUTOMATIZADO 💸 ---")
    print("Nota: Se ignora la complejidad de ME/TE del BPC y Fees de Planta para simplificar el cálculo.")
    
    try:
        # 1. Obtener todas las ofertas de la tienda Gallente FW
        url_offers = f"{ESI_BASE_URL}/loyalty/stores/{GALLENTE_FW_CORP_ID}/offers/"
        response = requests.get(url_offers)
        response.raise_for_status()
        offers = response.json()
        
        all_results = []
        
        for offer in offers:
            if 'lp_cost' in offer and offer['lp_cost'] > 0 and ('isk_cost' in offer or offer.get('required_items')):
                
                item_name = get_item_name(offer['type_id'])
                is_blueprint = "Blueprint" in item_name
                
                if is_blueprint:
                    # Cálculo 1: Reventa del BPC
                    resale_result = calculate_bpc_resale_profit(offer)
                    if resale_result:
                        all_results.append(resale_result)

                    # Cálculo 2: Fabricación del Producto Final
                    manuf_result = calculate_bpc_manufacturing_profit(offer)
                    if manuf_result:
                        all_results.append(manuf_result)
                        
                else:
                    # Cálculo 3: Venta Directa (Módulos, Ammo, etc.)
                    direct_result = calculate_lp_cost_and_profit(offer)
                    if direct_result:
                        all_results.append(direct_result)
        
        if not all_results:
            print("\n❌ No se encontraron ofertas rentables válidas en Jita.")
            return

        # Ordenar y seleccionar el mejor ítem
        all_results.sort(key=lambda x: x['ISK/LP'], reverse=True)
        best_item = all_results[0]
        
        # 2. Mostrar Resultados
        print(f"\nSe encontraron {len(all_results)} opciones de rentabilidad (incluyendo BPC).")
        print("\n--- 💰 Top 10 Rentabilidad ISK/LP (Venta Directa, Reventa BPC y Fabricación) 💰 ---")
        print("-----------------------------------------------------------------------------------------------------------------------------------")
        print(f"{'Item (Método)':<50} | {'ISK/LP':<8} | {'LP Cost':<8} | {'ISK Total Cost':<15} | {'Profit':<15} | {'Sell Price (Jita)':<15}")
        print("-----------------------------------------------------------------------------------------------------------------------------------")
        for res in all_results[:10]:
            print(
                f"{res['Item'] + ' (' + res['Method'] + ')':<50} | "
                f"{res['ISK/LP']:.0f}{' ISK':<4} | "
                f"{res['LP Cost']:,<8} | "
                f"{res['Total ISK Cost']:,<15.0f} | "
                f"{res['Profit']:,<15.0f} | "
                f"{res['Sell Price']:,<15.2f}"
            )
        print("-----------------------------------------------------------------------------------------------------------------------------------")

        print(f"\n✨ **MEJOR OPCIÓN TOTAL:** {best_item['Item']} ({best_item['Method']})")
        print(f"   -> Ratio ISK/LP: **{best_item['ISK/LP']:.0f}** ISK por LP")
        
    except requests.exceptions.RequestException as e:
        print(f"\n❌ ERROR FATAL AL CONSULTAR LA API: {e}")
        print("Asegúrate de tener conexión y que la ESI/EVEMarketer/Fuzzwork estén activos.")

if __name__ == "__main__":
    main()
