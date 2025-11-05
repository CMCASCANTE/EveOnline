import requests
import json
import time
from datetime import datetime, timedelta

# --- CONSTANTES DE EVE ---
ESI_BASE_URL = "https://esi.evetech.net/latest"
FDU_CORP_ID = 1000181 # Federal Defense Union (ID numérico para la API)
TYPE_ID_ISK = 58 # Type ID para ISK
VOLUME_DAYS = 10 
# Filtros de Rentabilidad
MIN_ISK_PER_LP_FILTER_LOW = 1000 
MIN_ISK_PER_LP_FILTER_HIGH = 2000 

# Definición de las regiones clave para el análisis
MARKETS = {
    "Jita": 10000002,
    "Dodixie": 10000032,
    "Amarr": 10000043,
    "Hek": 10000042,     
}

# --- LÍMITES DE RESULTADOS ---
TOP_N_REGIONAL = 15
TOP_N_GLOBAL = 25
TOP_N_VOLUME_FILTER = 25

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
    required_items = offer.get('required_items', [])
    
    for item in required_items:
        if item.get('type_id') != TYPE_ID_ISK: 
            return True
            
    return False

def get_market_stats(type_id, region_id):
    """ Obtiene estadísticas de 30 días, volumen de 10 días y precio actual para una región. """
    
    # 1. Obtener historial (30 días y volumen de 10 días)
    history_url = f"{ESI_BASE_URL}/markets/{region_id}/history/?datasource=tranquility&type_id={type_id}"
    
    avg_price, lowest_price, volume_10d = 0, float('inf'), 0
    
    try:
        history_response = requests.get(history_url, timeout=10)
        history_response.raise_for_status()
        history_data = history_response.json()
        
        thirty_days_ago = datetime.now() - timedelta(days=30)
        ten_days_ago = datetime.now() - timedelta(days=VOLUME_DAYS)
        
        avg_prices = []
        lowest_daily_prices = []
        
        for day in history_data:
            date_obj = datetime.strptime(day['date'], '%Y-%m-%d')
            
            if date_obj >= thirty_days_ago:
                avg_prices.append(day['average'])
                lowest_daily_prices.append(day['lowest']) 
            
            if date_obj >= ten_days_ago:
                volume_10d += day['volume'] 
        
        if avg_prices:
            avg_price = sum(avg_prices) / len(avg_prices)
            lowest_price = min(lowest_daily_prices)
        else:
            lowest_price = 0

    except requests.exceptions.RequestException:
        pass 

    # 2. Obtener precio actual (Min Sell)
    current_price_url = f"{ESI_BASE_URL}/markets/{region_id}/orders/?datasource=tranquility&order_type=sell&type_id={type_id}"
    current_sell_price = 0
    
    try:
        current_response = requests.get(current_price_url, timeout=5)
        current_response.raise_for_status() 
        data = current_response.json()
        sell_prices = [order.get('price', float('inf')) for order in data if order.get('price', 0) > 0]
        current_sell_price = min(sell_prices) if sell_prices else 0
    except requests.exceptions.RequestException:
        pass 
        
    return avg_price, lowest_price, current_sell_price, volume_10d

# --- LÓGICA DE IMPRESIÓN COMPARTIDA ---

def format_detailed_row(r, include_market=False):
    """ Formatea una fila de resultados con TODAS las columnas (Regional/Global), incluyendo ISK/LP ACTUAL. """
    market_col = f"{r['market']:<12} | " if include_market else ""
    
    return (
        f"{r['isk_per_lp']:<19,.0f} | "
        f"{r['isk_per_lp_current']:<19,.0f} | "  # <--- NUEVA COLUMNA DE DATOS
        f"{r['isk_profit']:<18,.0f} | "
        f"{r['lp_cost']:<14,} | "
        f"{r['isk_base_cost']:<14,} | "
        f"{market_col}"
        f"{r['sell_price_avg']:<18,.2f} | "
        f"{r['sell_price_low']:<18,.2f} | "
        f"{r['sell_price_current']:<18,.2f} | "
        f"{r['volume_10d']:<15,} | "
        f"{r['item_name']} ({r['quantity']})"
    )

def get_detailed_header(include_market=False):
    """ Genera el encabezado detallado con el padding ajustado, incluyendo ISK/LP ACTUAL. """
    market_col = f"{'Mercado':<12} | " if include_market else ""
    
    price_cols = (
        f"{'AVG (30D)':<18} | "  
        f"{'LOW (30D)':<18} | " 
        f"{'ACTUAL':<18} | "
    )
    
    return (
        f"{'ISK/LP AVG':<19} | "  # <--- NOMBRE AJUSTADO
        f"{'ISK/LP ACTUAL':<19} | " # <--- NUEVA COLUMNA DE ENCABEZADO
        f"{'Ganancia Neta':<18} | " 
        f"{'Costo LP':<14} | " 
        f"{'Costo ISK':<14} | " 
        f"{market_col}"
        f"{price_cols}{f'Volumen ({VOLUME_DAYS}D)':<15} | {'Item (Cant.)':<30}"
    )

def calculate_width(include_market):
    """ Calcula el ancho total de la tabla en base al padding (ajustado para la nueva columna). """
    # Se añade el padding y separador de la nueva columna (19 + 3)
    base_width = 19 + 19 + 18 + 14 + 14 + 18 + 18 + 18 + 15 + 30 
    separators = 9 * 3 # Ahora hay 9 separadores
    market_width = 15 if include_market else 0
    return base_width + separators + market_width

def print_results(title, results, top_n):
    """ Imprime la tabla de resultados formatada con TODAS las columnas (Regional). """
    
    WIDTH = calculate_width(include_market=False)
    print("\n" + "="*WIDTH)
    print(f"  {title}")
    print("="*WIDTH)
    
    print(get_detailed_header(include_market=False))
    print("-"*(WIDTH))
    
    for r in results[:top_n]:
        if r['isk_per_lp'] > 0:
            print(format_detailed_row(r, include_market=False))
    print("="*WIDTH)

def print_global_results_detailed(results, title, top_n, sort_key='isk_per_lp', min_lp_filter=0):
    """ Imprime la tabla de resultados globales con TODAS las columnas DETALLADAS. """
    
    # 1. Aplicar filtros (Siempre basado en la Rentabilidad AVG para la clasificación base)
    filtered_results_initial = [r for r in results if r['volume_10d'] > 0 and r['isk_per_lp'] >= min_lp_filter]
    
    # 2. Eliminar duplicados, manteniendo el mejor según el criterio de ordenación
    unique_results = {}
    
    # Si ordenamos por ISK/LP (AVG), mantenemos el máximo ISK/LP (AVG)
    if sort_key == 'isk_per_lp':
        for r in filtered_results_initial:
            if r['item_name'] not in unique_results or r['isk_per_lp'] > unique_results[r['item_name']]['isk_per_lp']:
                unique_results[r['item_name']] = r
    # Si ordenamos por Volumen, mantenemos el máximo Volumen
    elif sort_key == 'volume_10d':
        for r in filtered_results_initial:
            if r['item_name'] not in unique_results or r['volume_10d'] > unique_results[r['item_name']]['volume_10d']:
                unique_results[r['item_name']] = r
    
    results_to_print = list(unique_results.values())
    
    # 3. Ordenar
    results_to_print.sort(key=lambda x: x[sort_key], reverse=True)
    
    # 4. Título
    if min_lp_filter > 0:
        title_suffix = f" (Volumen > 0 y ISK/LP AVG $\ge$ {min_lp_filter:,.0f})"
    else:
        title_suffix = " (Volumen > 0, Top por ISK/LP AVG)"

    
    WIDTH = calculate_width(include_market=True)
    print("\n" + "="*WIDTH)
    print(f"  {title}{title_suffix} (Mostrando TOP {top_n} de {len(results_to_print)} resultados filtrados)")
    print("="*WIDTH)
    
    print(get_detailed_header(include_market=True))
    print("-"*(WIDTH))

    for r in results_to_print[:top_n]: 
        print(format_detailed_row(r, include_market=True))
    print("="*WIDTH)


# --- FUNCIÓN PRINCIPAL ---

def main():
    print(f"--- 💸 RENTABILIDAD MULTI-MERCADO LP+ISK (4 Capitales, V32) 💸 ---")
    
    # 1. Obtener y filtrar ofertas LP
    all_offers, error = get_lp_store_offers_esi(FDU_CORP_ID)
    if error:
        print(f"\n{error}")
        return
        
    filtered_offers = []
    for offer in all_offers:
        lp_cost = offer.get('lp_cost', 0)
        isk_base_cost = offer.get('isk_cost', 0)
        
        if lp_cost == 0: continue
        if is_material_required(offer): continue 
            
        offer['lp_cost_actual'] = lp_cost
        offer['isk_base_cost_actual'] = isk_base_cost
        filtered_offers.append(offer)

    if not filtered_offers:
        print("\n⚠️ No se encontraron ofertas que solo requieran LP e ISK base para esta corporación.")
        return

    print(f"✅ Ofertas filtradas: {len(filtered_offers)} (Solo LP+ISK, Corp {FDU_CORP_ID})")
    
    # 2. Consulta de nombres
    type_ids = [offer['type_id'] for offer in filtered_offers]
    item_names = {}
    try:
        name_response = requests.post(f"{ESI_BASE_URL}/universe/names/?datasource=tranquility", 
                                      json=type_ids, timeout=10)
        name_response.raise_for_status()
        for item in name_response.json():
            item_names[item['id']] = item['name']
    except Exception:
        pass 

    # 3. Calcular rentabilidad por mercado
    market_results = {}
    global_results = []
    
    for market_name, region_id in MARKETS.items():
        print(f"\n--- 📊 Analizando rentabilidad para el mercado: {market_name} (Region ID: {region_id}) ---")
        
        current_market_results = []
        
        for offer in filtered_offers:
            type_id = offer['type_id']
            lp_cost = offer['lp_cost_actual']
            isk_base_cost = offer['isk_base_cost_actual']
            quantity = offer.get('quantity', 1)
            item_name = item_names.get(type_id, f"ID: {type_id}")
            
            # Obtener estadísticas específicas de la región
            avg_price, lowest_price, current_sell_price, volume_10d = get_market_stats(type_id, region_id)
            
            # Cálculo de rentabilidad basado en AVG (para la columna de clasificación original)
            if avg_price > 0 or current_sell_price > 0:
                price_to_use_avg = avg_price if avg_price > 0 else current_sell_price
                
                total_sell_value_avg = price_to_use_avg * quantity
                isk_profit_avg = total_sell_value_avg - isk_base_cost
                isk_per_lp_avg = isk_profit_avg / lp_cost if lp_cost > 0 else 0
                
                # Cálculo de rentabilidad basado en ACTUAL (para la nueva columna)
                total_sell_value_current = current_sell_price * quantity
                isk_profit_current = total_sell_value_current - isk_base_cost
                isk_per_lp_current = isk_profit_current / lp_cost if lp_cost > 0 else 0
                
                result = {
                    "market": market_name, 
                    "item_name": item_name,
                    "isk_per_lp": isk_per_lp_avg,          # Usamos AVG como el ISK/LP principal
                    "isk_per_lp_current": isk_per_lp_current, # <--- NUEVA MÉTRICA
                    "isk_profit": isk_profit_avg,
                    "lp_cost": lp_cost,
                    "isk_base_cost": isk_base_cost,
                    "sell_price_avg": avg_price,
                    "sell_price_low": lowest_price,
                    "sell_price_current": current_sell_price,
                    "volume_10d": volume_10d,
                    "quantity": quantity
                }
                
                # Mantenemos el filtro principal de rentabilidad positiva (basado en AVG)
                if isk_per_lp_avg > 0:
                    current_market_results.append(result)
                    global_results.append(result)

            time.sleep(0.05) 

        # 4. Ordenar y guardar resultados regionales
        current_market_results.sort(key=lambda x: x['isk_per_lp'], reverse=True)
        market_results[market_name] = current_market_results

    # 5. Imprimir resultados por región (4 tablas)
    for market_name, results in market_results.items():
        print_results(f"TOP {TOP_N_REGIONAL} RENTABILIDAD LP+ISK - MERCADO: {market_name}", results, TOP_N_REGIONAL)
        
    # 6. Imprimir resultados globales (Tabla 5: TOP 25 por Rentabilidad AVG, Volumen > 0)
    print_global_results_detailed(
        global_results, 
        f"TABLA 5: TOP {TOP_N_GLOBAL} RENTABILIDAD MÁXIMA GLOBAL", 
        TOP_N_GLOBAL, 
        sort_key='isk_per_lp',
        min_lp_filter=0 
    )
    
    # 7. Imprimir resultados filtrados por Volumen (Tabla 6: ISK/LP AVG >= 1,000)
    print_global_results_detailed(
        global_results, 
        f"TABLA 6: TOP {TOP_N_VOLUME_FILTER} LIQUIDEZ MEDIA", 
        TOP_N_VOLUME_FILTER, 
        sort_key='volume_10d',
        min_lp_filter=MIN_ISK_PER_LP_FILTER_LOW # 1,000 ISK/LP
    )

    # 8. Imprimir resultados filtrados por Volumen (Tabla 7: ISK/LP AVG >= 2,000)
    print_global_results_detailed(
        global_results, 
        f"TABLA 7: TOP {TOP_N_VOLUME_FILTER} LIQUIDEZ ALTA", 
        TOP_N_VOLUME_FILTER, 
        sort_key='volume_10d',
        min_lp_filter=MIN_ISK_PER_LP_FILTER_HIGH # 2,000 ISK/LP
    )


if __name__ == "__main__":
    main()