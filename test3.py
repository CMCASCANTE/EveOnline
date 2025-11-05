import requests
import json
import time
from datetime import datetime, timedelta

# --- CONSTANTES DE EVE ---
ESI_BASE_URL = "https://esi.evetech.net/latest"
FDU_CORP_ID = 1000181 # Federal Defense Union (ID numérico para la API)
TYPE_ID_ISK = 58 # Type ID para ISK
VOLUME_DAYS = 10 

# Definición de las regiones clave para el análisis
MARKETS = {
    "Jita": 10000002,
    "Dodixie": 10000032,
    "Amarr": 10000043,
    "Hek": 10000042,     
    "Reno (Caldari)": 10000067  
}

# --- LÍMITES DE RESULTADOS ---
TOP_N_REGIONAL = 15
TOP_N_GLOBAL = 25

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

# --- LÓGICA DE IMPRESIÓN ---

# Esta función se usa para las tablas regionales (TOP 15)
def print_results(title, results, top_n):
    """ Imprime la tabla de resultados formatada con TODAS las columnas (Regional). """
    
    # Ancho necesario para TODAS las columnas
    WIDTH = 220 
    print("\n" + "="*WIDTH)
    print(f"  {title}")
    print("="*WIDTH)
    
    # Encabezado Detallado
    header = (
        f"{'Rentabilidad (ISK/LP)':<25} | {'Ganancia Neta (ISK)':<25} | {'Costo LP':<15} | {'Costo ISK':<15} | "
        f"{f'Volumen ({VOLUME_DAYS}D)':<18} | {'Precio Venta/Item ACTUAL':<27} | {'Precio Venta/Item AVG (30D)':<27} | {'Precio Venta/Item LOW (30D)':<27} | {'Item (Cant.)':<30}"
    )
    print(header)
    print("-"*WIDTH)
    
    for r in results[:top_n]:
        if r['isk_per_lp'] > 0:
            print(
                f"{r['isk_per_lp']:<25,.0f} | "
                f"{r['isk_profit']:<25,.0f} | "
                f"{r['lp_cost']:<15,} | "
                f"{r['isk_base_cost']:<15,} | "
                f"{r['volume_10d']:<18,} | "
                f"{r['sell_price_current']:<27,.2f} | "
                f"{r['sell_price_avg']:<27,.2f} | "
                f"{r['sell_price_low']:<27,.2f} | "
                f"{r['item_name']} ({r['quantity']})"
            )
    print("="*WIDTH)

# Esta función es NUEVA/MODIFICADA para la tabla GLOBAL (TOP 25)
def print_global_results_detailed(results, top_n):
    """ Imprime la tabla de resultados globales con TODAS las columnas DETALLADAS. """
    
    # Filtrar resultados con volumen > 0
    filtered_results = [r for r in results if r['volume_10d'] > 0]
    
    WIDTH = 230 # Ajustamos el ancho para incluir la columna 'Mercado'
    print("\n" + "="*WIDTH)
    print(f"  TOP {top_n} RENTABILIDAD LP+ISK - ANÁLISIS GLOBAL DETALLADO (Solo Volumen > 0)")
    print("="*WIDTH)
    
    # Encabezado Global DETALLADO (incluye todas las columnas)
    global_header = (
        f"{'Rentabilidad (ISK/LP)':<25} | {'Ganancia Neta (ISK)':<25} | {'Costo LP':<15} | {'Costo ISK':<15} | {'Mercado':<12} | "
        f"{f'Volumen ({VOLUME_DAYS}D)':<18} | {'Precio Venta/Item ACTUAL':<27} | {'Precio Venta/Item AVG (30D)':<27} | {'Precio Venta/Item LOW (30D)':<27} | {'Item (Cant.)':<30}"
    )
    print(global_header)
    print("-"*(WIDTH))

    for r in filtered_results[:top_n]: 
        if r['isk_per_lp'] > 0:
            print(
                f"{r['isk_per_lp']:<25,.0f} | "
                f"{r['isk_profit']:<25,.0f} | "
                f"{r['lp_cost']:<15,} | "
                f"{r['isk_base_cost']:<15,} | "
                f"{r['market']:<12} | " # Columna de Mercado
                f"{r['volume_10d']:<18,} | "
                f"{r['sell_price_current']:<27,.2f} | "
                f"{r['sell_price_avg']:<27,.2f} | "
                f"{r['sell_price_low']:<27,.2f} | "
                f"{r['item_name']} ({r['quantity']})"
            )
    print("="*WIDTH)


# --- FUNCIÓN PRINCIPAL ---

def main():
    print(f"--- 💸 RENTABILIDAD MULTI-MERCADO LP+ISK (Federal Defense Union, V26 - Tablas Detalladas) 💸 ---")
    
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
            
            if avg_price > 0 or current_sell_price > 0:
                price_to_use = avg_price if avg_price > 0 else current_sell_price
                
                total_sell_value_avg = price_to_use * quantity
                total_cost = isk_base_cost 
                
                isk_profit = total_sell_value_avg - total_cost
                isk_per_lp = isk_profit / lp_cost if lp_cost > 0 else 0
                
                result = {
                    "market": market_name, 
                    "item_name": item_name,
                    "isk_per_lp": isk_per_lp,
                    "isk_profit": isk_profit,
                    "lp_cost": lp_cost,
                    "isk_base_cost": isk_base_cost,
                    "sell_price_avg": avg_price,
                    "sell_price_low": lowest_price,
                    "sell_price_current": current_sell_price,
                    "volume_10d": volume_10d,
                    "quantity": quantity
                }
                
                if isk_per_lp > 0:
                    current_market_results.append(result)
                    global_results.append(result)

            time.sleep(0.05) 

        # 4. Ordenar y guardar resultados regionales
        current_market_results.sort(key=lambda x: x['isk_per_lp'], reverse=True)
        market_results[market_name] = current_market_results

    # 5. Imprimir resultados por región (TOP_N_REGIONAL)
    for market_name, results in market_results.items():
        print_results(f"TOP {TOP_N_REGIONAL} RENTABILIDAD LP+ISK - MERCADO: {market_name}", results, TOP_N_REGIONAL)
        
    # 6. Imprimir resultados globales (TOP_N_GLOBAL con filtro y DETALLADO)
    global_results.sort(key=lambda x: x['isk_per_lp'], reverse=True)
    print_global_results_detailed(global_results, TOP_N_GLOBAL)


if __name__ == "__main__":
    main()