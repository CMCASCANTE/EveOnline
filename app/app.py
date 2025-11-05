import requests
import time
import math
from datetime import datetime, timedelta
from flask import Flask, render_template, redirect, url_for, request, jsonify

app = Flask(__name__)

# --- CONSTANTES DE EVE ---
ESI_BASE_URL = "https://esi.evetech.net/latest" 
FDU_CORP_ID = 1000181 # Federal Defense Union (ID numérico para la API)
TYPE_ID_ISK = 58 # Type ID para ISK
VOLUME_DAYS = 10 
# Filtros de Rentabilidad (Criterio de "Margen Decente")
MIN_ISK_PER_LP_FILTER_LOW = 1000 
MIN_ISK_PER_LP_FILTER_HIGH = 2000 
# Tiempos de Volumen para el Modal (15, 10, 5 días) - Requerido por el usuario
VOLUME_TIMEFRAMES = [15, 10, 5] 

# Definición de las regiones clave para el análisis
MARKETS = {
    "Jita": 10000002,
    "Dodixie": 10000032,
    "Amarr": 10000043,
    "Hek": 10000042,     
}

# Límites de resultados
TOP_N_REGIONAL = 15
TOP_N_GLOBAL = 25
TOP_N_VOLUME_FILTER = 25
TOP_N_TRADING = 15 # Top 15 resultados para Trading Puro (Corregido en la versión anterior)
TOP_N_MODAL = 5 # Top 5 órdenes para el modal

# =========================================================
# === FUNCIONES ESI COMPARTIDAS Y LP (Para la ruta /analyze)
# =========================================================

def get_lp_store_offers_esi(corp_id):
    """ Obtiene TODAS las ofertas de la tienda LP de una corporación específica. """
    url = f"{ESI_BASE_URL}/loyalty/stores/{corp_id}/offers/?datasource=tranquility"
    try:
        headers = {'User-Agent': 'EVE LP Market Analyzer'}
        response = requests.get(url, headers=headers, timeout=15)
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

def get_lp_item_list():
    """ Obtiene la lista de IDs de items y sus nombres, solo para items LP/ISK. (Usado por Trading) """
    all_offers, error = get_lp_store_offers_esi(FDU_CORP_ID)
    if error: return {"error": error}, None
    
    filtered_offers = []
    for offer in all_offers:
        if offer.get('lp_cost', 0) == 0: continue
        if is_material_required(offer): continue 
        filtered_offers.append(offer)

    if not filtered_offers:
        return {"error": "⚠️ No se encontraron ofertas de items LP/ISK."}, None

    type_ids = [offer['type_id'] for offer in filtered_offers]
    item_names = {}
    try:
        name_response = requests.post(f"{ESI_BASE_URL}/universe/names/?datasource=tranquility", 
                                      json=type_ids, timeout=10)
        name_response.raise_for_status()
        for item in name_response.json():
            item_names[item['id']] = item['name']
    except Exception as e:
        # Se ignora el error de nombre si no es crítico, se usa el ID
        pass 

    return {
        "item_data": [
            {
                "type_id": offer['type_id'], 
                "item_name": item_names.get(offer['type_id'], f"ID: {offer['type_id']}"),
                "quantity": offer.get('quantity', 1)
            } for offer in filtered_offers
        ]
    }, None

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
            lowest_price = min(lowest_daily_prices) if lowest_daily_prices else 0
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
        
        sell_prices = [order.get('price', float('inf')) for order in data if order.get('is_buy_order') == False and order.get('price', 0) > 0]
        
        current_sell_price = min(sell_prices) if sell_prices else 0
    except requests.exceptions.RequestException:
        pass 
        
    return avg_price, lowest_price, current_sell_price, volume_10d

def run_market_analysis():
    """ Ejecuta todo el proceso de análisis de ESI. """
    
    # 1. Obtención y filtrado de ofertas LP 
    all_offers, error = get_lp_store_offers_esi(FDU_CORP_ID)
    if error: return {"error": error}
        
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
        return {"error": "⚠️ No se encontraron ofertas que solo requieran LP e ISK base para esta corporación."}

    # 2. Consulta de nombres
    type_ids = [offer['type_id'] for offer in filtered_offers]
    item_names = {}
    try:
        name_response = requests.post(f"{ESI_BASE_URL}/universe/names/?datasource=tranquility", 
                                      json=type_ids, timeout=10)
        name_response.raise_for_status()
        for item in name_response.json():
            item_names[item['id']] = item['name']
    except Exception as e:
        pass 

    # 3. Calcular rentabilidad por mercado
    market_results = {}
    global_results = []
    
    for market_name, region_id in MARKETS.items():
        current_market_results = []
        
        for offer in filtered_offers:
            type_id = offer['type_id']
            lp_cost = offer['lp_cost_actual']
            isk_base_cost = offer['isk_base_cost_actual']
            quantity = offer.get('quantity', 1)
            item_name = item_names.get(type_id, f"ID: {type_id}")
            
            avg_price, lowest_price, current_sell_price, volume_10d = get_market_stats(type_id, region_id)
            
            if avg_price > 0 or current_sell_price > 0:
                price_to_use_avg = avg_price if avg_price > 0 else current_sell_price
                total_sell_value_avg = price_to_use_avg * quantity
                isk_profit_avg = total_sell_value_avg - isk_base_cost
                isk_per_lp_avg = isk_profit_avg / lp_cost if lp_cost > 0 else 0
                
                total_sell_value_current = current_sell_price * quantity
                isk_profit_current = total_sell_value_current - isk_base_cost
                isk_per_lp_current = isk_profit_current / lp_cost if lp_cost > 0 else 0
                
                result = {
                    "market": market_name, 
                    "item_name": item_name,
                    "type_id": type_id, 
                    "isk_per_lp": isk_per_lp_avg,
                    "isk_per_lp_current": isk_per_lp_current, 
                    "isk_profit": isk_profit_avg,
                    "lp_cost": lp_cost,
                    "isk_base_cost": isk_base_cost,
                    "sell_price_avg": avg_price,
                    "sell_price_low": lowest_price,
                    "sell_price_current": current_sell_price,
                    "volume_10d": volume_10d,
                    "quantity": quantity
                }
                
                if isk_per_lp_avg > 0:
                    current_market_results.append(result)
                    global_results.append(result)

            time.sleep(0.02) 

        current_market_results.sort(key=lambda x: x['isk_per_lp'], reverse=True)
        market_results[market_name] = current_market_results[:TOP_N_REGIONAL]
    
    def process_global(results, sort_key, top_n, min_lp_filter):
        filtered = [r for r in results if r['volume_10d'] > 0 and r['isk_per_lp'] >= min_lp_filter]
        unique = {}
        for r in filtered:
            key = r['item_name']
            if key not in unique or r[sort_key] > unique[key][sort_key]:
                unique[key] = r
        
        final_list = list(unique.values())
        final_list.sort(key=lambda x: x[sort_key], reverse=True)
        return final_list[:top_n]

    
    global_max_isk = process_global(global_results, 'isk_per_lp', TOP_N_GLOBAL, 0)
    global_liquidez_media = process_global(global_results, 'volume_10d', TOP_N_VOLUME_FILTER, MIN_ISK_PER_LP_FILTER_LOW)
    global_liquidez_alta = process_global(global_results, 'volume_10d', TOP_N_VOLUME_FILTER, MIN_ISK_PER_LP_FILTER_HIGH)
    
    headers = [
        "ISK/LP AVG", "ISK/LP ACTUAL", "Ganancia Neta", "Costo LP", "Costo ISK", 
        "Mercado", "AVG (30D)", "LOW (30D)", "ACTUAL", f"Volumen ({VOLUME_DAYS}D)", "IA ANÁLISIS", "Item (Cant.)"
    ]

    return {
        "headers": headers,
        "market_results": market_results, 
        "global_max_isk": global_max_isk, 
        "global_liquidez_media": global_liquidez_media, 
        "global_liquidez_alta": global_liquidez_alta,
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S EVE Time")
    }

def get_orders_for_item_in_all_markets(type_id, markets=MARKETS):
    """
    Función auxiliar para obtener todas las órdenes de compra/venta de un item
    en todos los mercados principales.
    """
    all_orders = {}
    for market_name, region_id in markets.items():
        orders_url = f"{ESI_BASE_URL}/markets/{region_id}/orders/?datasource=tranquility&type_id={type_id}"
        orders_data = []
        try:
            response = requests.get(orders_url, timeout=10)
            response.raise_for_status()
            orders_data = response.json()
        except requests.exceptions.RequestException:
            pass 

        sell_orders = sorted([o for o in orders_data if not o.get('is_buy_order') and o.get('price', 0) > 0], key=lambda x: x['price'])
        buy_orders = sorted([o for o in orders_data if o.get('is_buy_order') and o.get('price', 0) > 0], key=lambda x: x['price'], reverse=True)
        
        # Añadir el nombre del mercado a cada orden para la consolidación global
        for order in sell_orders:
            order['market_name'] = market_name
        for order in buy_orders:
            order['market_name'] = market_name

        all_orders[market_name] = {
            'buy_orders': buy_orders,
            'sell_orders': sell_orders
        }
        time.sleep(0.01) # Pequeña pausa para no saturar la API
    return all_orders


def get_full_market_summary(type_id, item_name, source_market_name, markets=MARKETS, top_n=TOP_N_MODAL):
    """ 
    Obtiene datos REALES de ESI para el modal:
    - Órdenes de mercado actuales (top 5 Regional y Global).
    - Volumen histórico específico (15, 10, 5 días).
    """
    source_region_id = MARKETS.get(source_market_name)
    if not source_region_id:
        return {"summary_html": f"<p style='color: #ff3333;'>Error: Mercado '{source_market_name}' no encontrado.</p>"}

    # --- 1. Obtener Órdenes Actuales (Global y Regional) ---
    all_market_orders = get_orders_for_item_in_all_markets(type_id, markets)
    
    # 1.1 Consolidación Regional (del mercado de origen)
    regional_orders = all_market_orders.get(source_market_name, {'buy_orders': [], 'sell_orders': []})
    regional_top_buy = regional_orders['buy_orders'][:top_n]
    regional_top_sell = regional_orders['sell_orders'][:top_n]
    
    # 1.2 Consolidación Global
    global_sell_orders = []
    global_buy_orders = []

    for market_name in all_market_orders:
        global_sell_orders.extend(all_market_orders[market_name]['sell_orders'])
        global_buy_orders.extend(all_market_orders[market_name]['buy_orders'])

    # Global Top Sell (sorted by price ascending)
    global_top_sell = sorted(global_sell_orders, key=lambda x: x['price'])[:top_n]
    
    # Global Top Buy (sorted by price descending)
    global_top_buy = sorted(global_buy_orders, key=lambda x: x['price'], reverse=True)[:top_n]

    # --- 2. Obtener Historial de Volumen (15, 10, 5 días) ---
    history_url = f"{ESI_BASE_URL}/markets/{source_region_id}/history/?datasource=tranquility&type_id={type_id}"
    specific_volumes = {days: 0 for days in VOLUME_TIMEFRAMES}
    volume_html = ""

    try:
        history_response = requests.get(history_url, timeout=10)
        history_response.raise_for_status()
        history_data = history_response.json()
        
        for days in VOLUME_TIMEFRAMES:
            time_ago = datetime.now() - timedelta(days=days)
            # Sumar el volumen de los últimos 'days' días
            total_volume = sum(day['volume'] for day in history_data if datetime.strptime(day['date'], '%Y-%m-%d') >= time_ago)
            specific_volumes[days] = total_volume
            
        # Generar el HTML para los volúmenes
        for days in sorted(VOLUME_TIMEFRAMES, reverse=True): 
            volume = specific_volumes.get(days, 0)
            volume_formatted = "{:,.0f}".format(volume)
            volume_html += f"""
            <li style="margin-bottom: 8px; display: flex; justify-content: space-between; padding: 0 10px;">
                <span style="color: #ffeb3b; font-weight: bold;">Últimos {days} Días:</span>
                <span style="font-weight: bold; color: #4CAF50;">{volume_formatted} transacciones</span>
            </li>
            """

    except requests.exceptions.RequestException:
        volume_html = "<li>Error al obtener historial de volumen de ESI.</li>"

    volume_section_html = f"""
    <hr style="border-color:#444; margin: 25px 0;">
    <p style="font-size: 1.1em; font-weight: bold; color: #ffeb3b; text-align: center;">
        📦 Volumen de Transacciones Histórico en **{source_market_name}**
    </p>
    <ul style="list-style: none; padding: 10px 0; font-size: 1.1em; max-width: 400px; margin: 10px auto;">
        {volume_html}
    </ul>
    
    <p style="font-size: 0.9em; color: #888; margin-top: 20px; text-align: center;">
        Fuente: ESI. El volumen es el **total combinado** (compras + ventas) de transacciones por día.
    </p>
    """
    
    # --- 3. Generar HTML para Órdenes ---
    
    def generate_orders_html(orders, color, title, show_market=False):
        list_items = ""
        if orders:
            for order in orders:
                price_formatted = "{:,.2f} ISK".format(order['price'])
                volume_formatted = "{:,.0f}".format(order['volume_remain'])
                # Muestra el nombre del mercado si es una orden global
                market_info = f" ({order['market_name']})" if show_market else ""
                list_items += f"<li>**{price_formatted}** {market_info} (Volumen: {volume_formatted})</li>"
        else:
             list_items = "<li>No hay órdenes activas.</li>"
             
        return f"""
        <div style="flex: 1; min-width: 45%;">
            <h4 style="color: {color}; margin-bottom: 5px;">{title}</h4>
            <ul style="list-style: none; padding: 0; margin: 0;">
                {list_items}
            </ul>
        </div>
        """
        
    # Contenido Global (Amarillo/Naranja)
    global_sell_html = generate_orders_html(global_top_sell, '#ffeb3b', 'VENTA (Global - Más Baratas)', show_market=True)
    global_buy_html = generate_orders_html(global_top_buy, '#ff9800', 'COMPRA (Global - Más Altas)', show_market=True)

    # Contenido Regional (Verde/Rojo)
    regional_sell_html = generate_orders_html(regional_top_sell, '#4CAF50', f'VENTA ({source_market_name})')
    regional_buy_html = generate_orders_html(regional_top_buy, '#ff3333', f'COMPRA ({source_market_name})')
    
    # Combinar las secciones
    global_section = f"""
    <p style="color: #ffeb3b; font-weight: bold; margin-top: 15px; text-align: center; border-bottom: 1px solid #ffeb3b44; padding-bottom: 10px;">
        🌍 Órdenes **GLOBALES** (Top {top_n} de {', '.join(markets.keys())})
    </p>
    <div style="display: flex; justify-content: space-around; flex-wrap: wrap; gap: 10px; font-size: 0.9em; text-align: left;">
        {global_sell_html}
        {global_buy_html}
    </div>
    """
    
    regional_section = f"""
    <p style="color: #1E90FF; font-weight: bold; margin-top: 25px; text-align: center; border-bottom: 1px solid #1E90FF44; padding-bottom: 10px;">
        📍 Órdenes **REGIONALES** en **{source_market_name}**
    </p>
    <div style="display: flex; justify-content: space-around; flex-wrap: wrap; gap: 10px; font-size: 0.9em; text-align: left;">
        {regional_sell_html}
        {regional_buy_html}
    </div>
    """

    summary_html = f"""
    <h2>Análisis de Mercado Detallado</h2>
    <h3 style="color: #1E90FF;">{item_name} (Item ID: {type_id})</h3>
    {global_section}
    {regional_section}
    {volume_section_html}
    """
    
    return {"summary_html": summary_html}


# --- RUTA API PARA EL MODAL ---
@app.route('/market_summary_api', methods=['POST'])
def market_summary_api():
    """ Procesa la solicitud del modal para el resumen de mercado. """
    data = request.json
    type_id = data.get('type_id')
    item_name = data.get('item_name') 
    source_market_name = data.get('source_market_name') 
    
    if not type_id or not source_market_name or not item_name:
        return jsonify({"summary_html": "<p>Error: ID de Item, Nombre de Item o Mercado no proporcionado.</p>"}), 400
    
    try:
        type_id_int = int(type_id)
    except ValueError:
        return jsonify({"summary_html": "<p>Error: ID de Item inválido.</p>"}), 400

    # Llama a la función que devuelve el análisis ESI real (ahora con lógica Global/Regional)
    market_data = get_full_market_summary(type_id_int, item_name, source_market_name)
    
    return jsonify(market_data)


# =========================================================
# === FUNCIONES Y RUTA DE TRADING PURO (Multi-Mercado)
# =========================================================

def get_trading_market_stats(type_id, region_id):
    """ Obtiene precio actual (Min Sell, Max Buy) y volumen de 10 días para una región. """
    history_url = f"{ESI_BASE_URL}/markets/{region_id}/history/?datasource=tranquility&type_id={type_id}"
    volume_10d = 0
    try:
        history_response = requests.get(history_url, timeout=10)
        history_response.raise_for_status()
        history_data = history_response.json()
        ten_days_ago = datetime.now() - timedelta(days=VOLUME_DAYS)
        for day in history_data:
            date_obj = datetime.strptime(day['date'], '%Y-%m-%d')
            if date_obj >= ten_days_ago:
                volume_10d += day['volume'] 
    except requests.exceptions.RequestException: pass 
        
    orders_url = f"{ESI_BASE_URL}/markets/{region_id}/orders/?datasource=tranquility&type_id={type_id}"
    min_sell_price, max_buy_price = 0, 0
    
    try:
        orders_response = requests.get(orders_url, timeout=5)
        orders_response.raise_for_status() 
        data = orders_response.json()
        
        sell_prices = [order.get('price', float('inf')) for order in data if not order.get('is_buy_order') and order.get('price', 0) > 0]
        buy_prices = [order.get('price', 0) for order in data if order.get('is_buy_order') and order.get('price', 0) > 0]
                
        min_sell_price = min(sell_prices) if sell_prices else 0
        max_buy_price = max(buy_prices) if buy_prices else 0
        
    except requests.exceptions.RequestException: pass 
        
    return max_buy_price, min_sell_price, volume_10d

def run_pure_trading_analysis():
    """ 
    Identifica oportunidades de trading puro para TODOS los mercados principales
    y genera un resumen global. 
    """
    item_data_result, error = get_lp_item_list() 
    if error: return {"error": error}
    item_data_list = item_data_result["item_data"]
    
    market_trading_results = {}
    global_all_results = []
    
    # 1. Iterar sobre todos los mercados definidos
    for market_name, region_id in MARKETS.items():
        current_market_results = []
        
        for item_data in item_data_list:
            type_id = item_data['type_id']
            item_name = item_data['item_name']
            
            max_buy, min_sell, volume_10d = get_trading_market_stats(type_id, region_id)
            
            # Filtro mínimo de liquidez y spread positivo
            if min_sell > max_buy and max_buy > 0 and volume_10d >= 1000: 
                profit_per_unit = min_sell - max_buy
                roi = (profit_per_unit / max_buy) * 100 
                spread_percent = (profit_per_unit / min_sell) * 100
                
                result = {
                    "market": market_name, # Añadido el mercado
                    "item_name": item_name,
                    "type_id": type_id,
                    "buy_price": max_buy,
                    "sell_price": min_sell,
                    "profit_per_unit": profit_per_unit,
                    "roi_percent": roi,
                    "spread_percent": spread_percent,
                    "volume_10d": volume_10d
                }
                current_market_results.append(result)
                global_all_results.append(result)
                
            time.sleep(0.02) 

        # Ordenar resultados por mercado: Arbitraje (Spread) y limitar a TOP_N
        current_market_results.sort(key=lambda x: x['spread_percent'], reverse=True)
        market_trading_results[market_name] = current_market_results[:TOP_N_TRADING]

    # 2. Análisis Global (Consolidación)
    
    # Arbitraje Global: El mejor spread% encontrado para CADA item en CUALQUIER mercado
    global_arbitrage = {}
    for r in global_all_results:
        key = r['item_name']
        if key not in global_arbitrage or r['spread_percent'] > global_arbitrage[key]['spread_percent']:
            global_arbitrage[key] = r

    final_global_arbitrage = list(global_arbitrage.values())
    final_global_arbitrage.sort(key=lambda x: x['spread_percent'], reverse=True)

    # Volumen Global: El mayor volumen encontrado para CADA item en CUALQUIER mercado
    global_volume = {}
    for r in global_all_results:
        key = r['item_name']
        if key not in global_volume or r['volume_10d'] > global_volume[key]['volume_10d']:
            global_volume[key] = r
            
    final_global_volume = list(global_volume.values())
    final_global_volume.sort(key=lambda x: x['volume_10d'], reverse=True)
    
    headers = ['Item', 'Mercado', 'Buy Price (Max)', 'Sell Price (Min)', 'Profit/Unit', 'ROI %', f'Volume {VOLUME_DAYS}d', 'Spread %']

    return {
        "headers": headers,
        "market_results": market_trading_results, 
        "global_arbitrage": final_global_arbitrage[:TOP_N_TRADING], 
        "global_volume": final_global_volume[:TOP_N_TRADING],
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S EVE Time"),
        "markets": list(MARKETS.keys()) # Para usar en la navegación HTML
    }


# =========================================================
# === RUTAS DE FLASK
# =========================================================

@app.route('/')
def home():
    """ Muestra la página de inicio con los botones. """
    return render_template('index.html')

@app.route('/loading')
def loading():
    """ Muestra la animación de carga y redirige al análisis usando un parámetro. """
    dest = request.args.get('dest', 'analyze')
    
    if dest == 'trading':
        redirect_url = url_for('trading_analysis')
    else:
        redirect_url = url_for('analyze')
        
    # El template loading.html usa redirect_url para el JS de redirección
    return render_template('loading.html', redirect_url=redirect_url)


@app.route('/analyze')
def analyze():
    """ Ejecuta el análisis de LP y muestra los resultados. """
    
    referrer = request.referrer if request.referrer else ''
    if not any(url_part in referrer for url_part in ['loading', 'analyze']):
         return redirect(url_for('loading', dest='analyze')) 

    analysis_data = run_market_analysis()
    
    if "error" in analysis_data:
        return render_template('analysis.html', error=analysis_data['error'], current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S EVE Time"))
    
    return render_template('analysis.html', data=analysis_data)


@app.route('/trading')
def trading_analysis():
    """ Ejecuta el análisis de trading puro y muestra los resultados. """
    
    referrer = request.referrer if request.referrer else ''
    if not any(url_part in referrer for url_part in ['loading', 'trading']):
         return redirect(url_for('loading', dest='trading')) 
         
    trading_data = run_pure_trading_analysis()
    
    if "error" in trading_data:
        return render_template('trading_analysis.html', error=trading_data['error'], current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S EVE Time"))

    return render_template('trading_analysis.html', data=trading_data)


if __name__ == '__main__':
    app.run(debug=True)

application = app