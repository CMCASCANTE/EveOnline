import requests

# IDs clave para Gallente Factional Warfare
GALLENTE_FW_CORP_ID = 1000037  # Federal Navy Academy (Una de las corporaciones clave de Gallente FW)
JITA_REGION_ID = 10000002       # The Forge (Región que contiene Jita)
JITA_STATION_ID = 60003760      # Jita IV - Moon 4 - Caldari Navy Assembly Plant

# Tasa de impuesto de venta estimada (para un cálculo conservador de la ganancia)
SALES_TAX_RATE = 0.05 

# Endpoint base de ESI
ESI_BASE_URL = "https://esi.evetech.net/latest"

def fetch_lp_store_offers(corporation_id):
    """
    Obtiene todas las ofertas (items y costos) de la tienda de LP de la corporación.
    """
    print(f"1. Consultando ofertas de LP para Corp ID: {corporation_id}...")
    url = f"{ESI_BASE_URL}/loyalty/stores/{corporation_id}/offers/"
    response = requests.get(url)
    response.raise_for_status()
    
    offers = response.json()
    print(f"   -> Encontradas {len(offers)} ofertas.")
    return offers

def get_market_sell_price(type_id, region_id):
    """
    Obtiene el precio de venta más bajo (Sell Order Min) en la región (Jita).
    
    Nota: Se consulta solo el precio de venta más bajo (quick sell price).
    Para un cálculo más preciso se debe obtener el historial o todas las órdenes.
    """
    # ESI: /markets/{region_id}/orders/
    # Filtramos por orders de venta (is_buy_order=false) para obtener el precio al que vendemos.
    print(f"2. Obteniendo órdenes de venta para Type ID {type_id}...")
    
    # Obtenemos las órdenes de mercado en Jita (solo las de venta)
    orders_url = f"{ESI_BASE_URL}/markets/{region_id}/orders/?type_id={type_id}&order_type=sell"
    response = requests.get(orders_url)
    response.raise_for_status()
    
    orders = response.json()
    
    if not orders:
        return 0.0
    
    # Encontramos el precio de venta más bajo (el mejor precio que obtendrías)
    min_sell_price = min(order['price'] for order in orders)
    
    return min_sell_price

def get_item_name(type_id):
    """
    Obtiene el nombre de un item a partir de su Type ID usando el endpoint ESI /universe/types/{type_id}.
    """
    url = f"{ESI_BASE_URL}/universe/types/{type_id}/"
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json().get('name', f"Unknown Item ({type_id})")
    except requests.exceptions.RequestException:
        return f"Unknown Item ({type_id})"

def calculate_isk_per_lp(offer):
    """
    Calcula el ISK/LP para una oferta de la tienda.
    """
    type_id = offer['type_id']
    lp_cost = offer.get('lp_cost', 0)
    isk_cost = offer.get('isk_cost', 0)
    
    if lp_cost == 0:
        return 0.0, 0.0, 0.0 # No tiene sentido si no cuesta LP

    item_name = get_item_name(type_id)
    sell_price = get_market_sell_price(type_id, JITA_REGION_ID)

    if sell_price == 0.0:
        print(f"   -> ADVERTENCIA: No hay órdenes de venta activas para {item_name} en Jita.")
        return 0.0, 0.0, item_name

    # Ganancia estimada: (Precio de venta * (1 - impuestos)) - Costo ISK
    estimated_revenue = sell_price * (1 - SALES_TAX_RATE)
    isk_profit = estimated_revenue - isk_cost
    
    isk_per_lp = isk_profit / lp_cost
    
    return isk_per_lp, isk_profit, item_name

def main():
    try:
        # 1. Obtener todas las ofertas de la tienda Gallente FW
        offers = fetch_lp_store_offers(GALLENTE_FW_CORP_ID)
        
        results = []
        for offer in offers:
            # Algunas ofertas requieren artículos en lugar de ISK; las ignoramos por simplicidad
            if 'isk_cost' in offer:
                isk_per_lp, isk_profit, item_name = calculate_isk_per_lp(offer)
                
                if isk_per_lp > 0:
                    results.append({
                        "Item": item_name,
                        "ISK/LP": isk_per_lp,
                        "Profit": isk_profit,
                        "LP Cost": offer.get('lp_cost', 0),
                        "ISK Cost": offer.get('isk_cost', 0),
                        "Sell Price": get_market_sell_price(offer['type_id'], JITA_REGION_ID)
                    })
        
        if not results:
            print("\n❌ No se encontraron ofertas rentables con costo en ISK/LP.")
            return

        # 3. Encontrar el artículo más rentable
        best_item = max(results, key=lambda x: x['ISK/LP'])
        
        # 4. Mostrar resultados
        
        # Ordenar por rentabilidad ISK/LP
        results.sort(key=lambda x: x['ISK/LP'], reverse=True)
        
        print("\n--- 💰 Top 5 Artículos más Rentables (ISK/LP) 💰 ---")
        print("---------------------------------------------------------------------------------------------------")
        print(f"{'Item':<35} | {'ISK/LP':<8} | {'LP Cost':<8} | {'ISK Cost':<12} | {'Sell Price (Jita)':<15}")
        print("---------------------------------------------------------------------------------------------------")
        for res in results[:5]:
            print(
                f"{res['Item']:<35} | "
                f"{res['ISK/LP']:.0f}{' ISK':<4} | "
                f"{res['LP Cost']:,<8} | "
                f"{res['ISK Cost']:,<12} | "
                f"{res['Sell Price']:,<15.2f}"
            )
        print("---------------------------------------------------------------------------------------------------")
        
        print(f"\n✨ **MEJOR OPCIÓN:** {best_item['Item']}")
        print(f"   -> Ratio ISK/LP: **{best_item['ISK/LP']:.0f}**")
        print(f"   -> Ganancia (Profit) por unidad: **{best_item['Profit']:.2f} ISK** (Estimado con 5% de impuestos)")
        
    except requests.exceptions.RequestException as e:
        print(f"\n❌ ERROR FATAL AL CONSULTAR LA ESI: {e}")
        print("Asegúrate de tener conexión y que la ESI esté activa.")

if __name__ == "__main__":
    main()