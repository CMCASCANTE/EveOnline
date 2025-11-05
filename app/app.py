import requests
import time
import math
from datetime import datetime, timedelta
from flask import Flask, render_template, redirect, url_for, request, jsonify

app = Flask(__name__)

# --- CONSTANTES DE EVE ---
# ¡CORRECCIÓN APLICADA AQUÍ! La URL correcta es 'evetech.net'
ESI_BASE_URL = "https://esi.evetech.net/latest" 
FDU_CORP_ID = 1000181 # Federal Defense Union (ID numérico para la API)
TYPE_ID_ISK = 58 # Type ID para ISK
VOLUME_DAYS = 10 
# Filtros de Rentabilidad
MIN_ISK_PER_LP_FILTER_LOW = 1000 
MIN_ISK_PER_LP_FILTER_HIGH = 2000 
# Tiempos de Volumen para el Modal (15, 10, 5 días)
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
TOP_N_TRADING = 5 # Top 5 resultados para Trading Puro

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

# =========================================================
# === LÓGICA DE ANÁLISIS DE RENTABILIDAD LP (run_market_analysis)
# =========================================================

def run_market_analysis():
    """ Ejecuta todo el proceso de análisis de ESI. """
    
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

    type_ids = [offer['type_id'] for offer in filtered_offers]
    item_names = {}
    try:
        name_response = requests.post(f"{ESI_BASE_URL}/universe/names/?datasource=tranquility", 
                                      json=type_ids, timeout=10)
        name_response.raise_for_status()
        for item in name_response.json():
            item_names[item['id']] = item['name']
    except Exception as e:
        print(f"Error al obtener nombres: {e}") 

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
            if key not in unique:
                unique[key] = r
            elif sort_key == 'isk_per_lp' and r['isk_per_lp'] > unique[key]['isk_per_lp']:
                unique[key] = r
            elif sort_key == 'volume_10d' and r['volume_10d'] > unique[key]['volume_10d']:
                unique[key] = r
        
        final_list = list(unique.values())
        final_list.sort(key=lambda x: x[sort_key], reverse=True)
        return final_list[:top_n]

    global_max_isk = process_global(global_results, 'isk_per_lp', TOP_N_GLOBAL, 0)
    global_liquidez_media = process_global(global_results, 'volume_10d', TOP_N_VOLUME_FILTER, MIN_ISK_PER_LP_FILTER_LOW)
    global_liquidez_alta = process_global(global_results, 'volume_10d', TOP_N_VOLUME_FILTER, MIN_ISK_PER_LP_FILTER_HIGH)
    
    headers = [
        "ISK/LP AVG", "ISK/LP ACTUAL", "Ganancia Neta", "Costo LP", "Costo ISK", 
        "Mercado", "AVG (30D)", "LOW (30D)", "ACTUAL", f"Volumen ({VOLUME_DAYS}D)", "ANÁLISIS ESI", "Item (Cant.)" 
    ]

    return {
        "headers": headers,
        "market_results": market_results, 
        "global_max_isk": global_max_isk, 
        "global_liquidez_media": global_liquidez_media, 
        "global_liquidez_alta": global_liquidez_alta,
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S EVE Time")
    }

# =========================================================
# === FUNCIONES DE TRADING PURO (Para la ruta /trading)
# =========================================================

def get_market_bid_ask_volume(type_id, region_id):
    """ Obtiene Max Buy (Bid), Min Sell (Ask) y el volumen de 10 días, esencial para el Trading. """
    
    # 1. Obtener Historial (para el volumen)
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
    except requests.exceptions.RequestException:
        pass 

    # 2. Obtener órdenes (para Bid y Ask)
    orders_url = f"{ESI_BASE_URL}/markets/{region_id}/orders/?datasource=tranquility&order_type=all&type_id={type_id}"
    min_sell_price, max_buy_price = 0, 0
    
    try:
        orders_response = requests.get(orders_url, timeout=5)
        orders_response.raise_for_status() 
        data = orders_response.json()
        
        sell_prices = []
        buy_prices = []
        
        for order in data:
            if order.get('price', 0) > 0 and order.get('volume_remain', 0) > 0:
                if order.get('is_buy_order'):
                    buy_prices.append(order['price'])
                else:
                    sell_prices.append(order['price'])
        
        min_sell_price = min(sell_prices) if sell_prices else 0
        max_buy_price = max(buy_prices) if buy_prices else 0
        
    except requests.exceptions.RequestException:
        pass 
        
    return max_buy_price, min_sell_price, volume_10d

def run_pure_trading_analysis():
    """ Identifica oportunidades de trading puro (especulación local) basadas en Spread % y Liquidez. """
    
    item_data_result, error = get_lp_item_list()
    if error:
        return {"error": error}
    
    item_data_list = item_data_result["item_data"]
    
    market_results = {market_name: [] for market_name in MARKETS}
    
    for market_name, region_id in MARKETS.items():
        
        for item in item_data_list:
            type_id = item['type_id']
            item_name = item['item_name']
            quantity = item['quantity']
            
            max_buy_price, min_sell_price, volume_10d = get_market_bid_ask_volume(type_id, region_id)
            
            if min_sell_price > 0 and max_buy_price > 0 and volume_10d > 0:
                spread = min_sell_price - max_buy_price
                base_price = max_buy_price
                spread_percent = (spread / base_price) * 100 if base_price > 0 else 0
                
                if volume_10d > 0:
                    liquidity_factor = math.sqrt(volume_10d) 
                else:
                    liquidity_factor = 0
                    
                profitability_score = spread_percent * liquidity_factor
                
                if profitability_score > 0 and spread_percent >= 2: 
                    result = {
                        "market": market_name, 
                        "item_name": item_name,
                        "type_id": type_id,
                        "spread_isk": spread * quantity,
                        "spread_percent": spread_percent,
                        "max_buy_price": max_buy_price, 
                        "min_sell_price": min_sell_price, 
                        "volume_10d": volume_10d,
                        "profitability_score": profitability_score,
                        "quantity": quantity
                    }
                    market_results[market_name].append(result)
            
            time.sleep(0.02) 

        market_results[market_name].sort(key=lambda x: x['profitability_score'], reverse=True)
        market_results[market_name] = market_results[market_name][:TOP_N_TRADING]

    headers = [
        "Score Liquidez", "Spread %", "Spread ISK (Uni)", "Máx. Compra (Bid)", 
        "Mín. Venta (Ask)", f"Volumen ({VOLUME_DAYS}D)", "Item (Cant.)"
    ]

    return {
        "headers": headers,
        "market_results": market_results, 
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S EVE Time")
    }

# =========================================================
# === FUNCIONES ESI PARA EL MODAL (Asegura que el botón 'ANÁLISIS ESI' funciona)
# =========================================================

def get_market_history_volume(type_id, region_id):
    """ Obtiene el volumen combinado para los periodos definidos. """
    history_url = f"{ESI_BASE_URL}/markets/{region_id}/history/?datasource=tranquility&type_id={type_id}"
    volumes = {days: 0 for days in VOLUME_TIMEFRAMES}
    
    try:
        history_response = requests.get(history_url, timeout=10)
        history_response.raise_for_status()
        history_data = history_response.json()
        
        time_thresholds = {days: datetime.now() - timedelta(days=days) for days in VOLUME_TIMEFRAMES}
        
        for day in history_data:
            date_obj = datetime.strptime(day['date'], '%Y-%m-%d')
            
            for days_key in VOLUME_TIMEFRAMES:
                if date_obj >= time_thresholds[days_key]:
                    volumes[days_key] += day['volume']
                    
    except requests.exceptions.RequestException:
        pass 
        
    return volumes


def get_market_orders(type_id, region_id, top_n=5):
    """ Obtiene las N órdenes de venta más bajas y N órdenes de compra más altas para una región específica. """
    orders_url = f"{ESI_BASE_URL}/markets/{region_id}/orders/?datasource=tranquility&type_id={type_id}"
    buy_orders = []
    sell_orders = []
    
    try:
        orders_response = requests.get(orders_url, timeout=15)
        orders_response.raise_for_status()
        all_orders = orders_response.json()
        
        for order in all_orders:
            if order.get('price', 0) <= 0 or order.get('volume_remain', 0) <= 0:
                continue
                
            order_data = {
                'price': order['price'],
                'volume': order['volume_remain'],
                'location_id': order.get('location_id')
            }
            
            if order.get('is_buy_order'):
                buy_orders.append(order_data)
            else:
                sell_orders.append(order_data)
                
        sell_orders.sort(key=lambda x: x['price'])
        buy_orders.sort(key=lambda x: x['price'], reverse=True)
        
        return sell_orders[:top_n], buy_orders[:top_n]

    except requests.exceptions.RequestException:
        return [], []


def get_full_market_summary(type_id, source_market_name, markets=MARKETS, top_n=5):
    """ Obtiene órdenes globales, específicas y volumen multi-días para el modal. """
    all_sell_orders = []
    all_buy_orders = []
    
    for market_name, region_id in markets.items():
        orders_url = f"{ESI_BASE_URL}/markets/{region_id}/orders/?datasource=tranquility&type_id={type_id}"
        
        try:
            orders_response = requests.get(orders_url, timeout=15)
            orders_response.raise_for_status()
            all_orders = orders_response.json()
            
            for order in all_orders:
                if order.get('price', 0) <= 0 or order.get('volume_remain', 0) <= 0:
                    continue
                    
                order_data = {
                    'price': order['price'],
                    'volume': order['volume_remain'],
                    'market': market_name,
                    'location_id': order.get('location_id')
                }
                
                if order.get('is_buy_order'):
                    all_buy_orders.append(order_data)
                else:
                    all_sell_orders.append(order_data)
        except requests.exceptions.RequestException:
            pass 

    all_sell_orders.sort(key=lambda x: x['price'])
    top_global_sell_orders = all_sell_orders[:top_n]
    
    all_buy_orders.sort(key=lambda x: x['price'], reverse=True)
    top_global_buy_orders = all_buy_orders[:top_n]
    
    source_region_id = markets.get(source_market_name)
    if not source_region_id:
        return {"error": f"Mercado fuente '{source_market_name}' no encontrado."}, None, None
        
    top_specific_sell_orders, top_specific_buy_orders = get_market_orders(type_id, source_region_id, top_n=top_n)
    specific_volumes = get_market_history_volume(type_id, source_region_id)

    return {
        'top_global_sell_orders': top_global_sell_orders,
        'top_global_buy_orders': top_global_buy_orders,
        'top_specific_sell_orders': top_specific_sell_orders,
        'top_specific_buy_orders': top_specific_buy_orders,
    }, specific_volumes, source_market_name

# =========================================================
# === RUTAS DE FLASK
# =========================================================

@app.route('/')
def home():
    """ Muestra la página de inicio con los dos botones. """
    return render_template('index.html')

@app.route('/loading')
def loading():
    """ Muestra la animación de carga para el análisis LP, y redirige a 'analyze'. """
    return render_template('loading.html', target_route='analyze')

@app.route('/loading-trading')
def loading_trading():
    """ Muestra la animación de carga y redirige al análisis de trading. """
    # target_route debe coincidir con el nombre de la función @app.route('/trading')
    return render_template('loading.html', target_route='trading_analysis') 

@app.route('/analyze')
def analyze():
    """ Ejecuta el análisis de la ESI para LP y muestra los resultados. """
    
    referrer = request.referrer if request.referrer else ''
    if not any(url_part in referrer for url_part in ['loading', 'analyze']):
         return redirect(url_for('loading')) 

    analysis_data = run_market_analysis() 
    
    if "error" in analysis_data:
        return render_template('analysis.html', error=analysis_data['error'], current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S EVE Time"))
    
    return render_template('analysis.html', data=analysis_data)


@app.route('/trading')
def trading_analysis():
    """ Ejecuta el análisis de Trading Puro y muestra los resultados. """
    
    referrer = request.referrer if request.referrer else ''
    if not any(url_part in referrer for url_part in ['loading-trading', 'trading']):
         return redirect(url_for('loading_trading')) 

    trading_data = run_pure_trading_analysis()
    
    if "error" in trading_data:
        return render_template('trading_analysis.html', error=trading_data['error'], current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S EVE Time"))

    return render_template('trading_analysis.html', data=trading_data)

@app.route('/api/market-summary', methods=['POST'])
def market_summary_api():
    """ Ruta para manejar la solicitud AJAX del modal (solo LP). """
    data = request.get_json()
    type_id = data.get('type_id')
    item_name = data.get('item_name')
    source_market_name = data.get('source_market_name') 
    
    if not type_id or not source_market_name:
        return jsonify({"summary_html": "<p>Error: ID de Item o Mercado no proporcionado.</p>"}), 400

    try:
        type_id_int = int(type_id)
    except ValueError:
        return jsonify({"summary_html": "<p>Error: ID de Item inválido.</p>"}), 400

    market_data, specific_volumes, source_market_name = get_full_market_summary(
        type_id_int, 
        source_market_name, 
        markets=MARKETS, 
        top_n=5
    )
    
    if "error" in market_data:
        return jsonify({"summary_html": f"<p style='color: #ff3333;'>❌ Error ESI: {market_data['error']}</p>"}), 500

    
    # --- Lógica de renderizado HTML del modal ---
    def format_orders(orders, is_global):
        html = '<table style="width: 100%; border-collapse: collapse; font-size: 1.0em;">'
        html += '<tr style="border-bottom: 2px solid #555; font-size: 0.9em;">'
        html += '<th style="text-align: left; padding: 5px 0;">Precio</th>'
        html += '<th style="padding: 5px 0; text-align: center;">Volumen</th>'
        if is_global:
            html += '<th style="text-align: right; padding: 5px 0;">Mercado</th>'
        html += '</tr>'
        
        if not orders:
             return "<p style='color: #888;'>No hay órdenes disponibles.</p>"
             
        is_sell_order = orders == market_data.get('top_global_sell_orders') or orders == market_data.get('top_specific_sell_orders')
        color = '#00ff00' if is_sell_order else '#ff3333'
            
        for order in orders:
            price_formatted = "{:,.2f}".format(order['price'])
            volume_formatted = "{:,.0f}".format(order['volume'])
            
            html += f'<tr style="border-bottom: 1px dashed #333;">'
            html += f'<td style="color: {color}; font-weight: bold; text-align: left; padding: 5px 0;">{price_formatted} ISK</td>'
            html += f'<td style="color: #eee; text-align: center; padding: 5px 0;">{volume_formatted} u.</td>'
            if is_global:
                html += f'<td style="color: #ffeb3b; font-weight: bold; text-align: right; padding: 5px 0;">{order.get("market", "")}</td>'
            html += f'</tr>'

        html += '</table>'
        return html

    global_orders_html = f"""
        <h2 style="color: #1E90FF; text-align: center; margin-top: 5px; border-bottom: 2px solid #1E90FF44; padding-bottom: 5px;">
            Órdenes TOP 5 Globales (Jita, Dodixie, Amarr, Hek)
        </h2>
        <div style="display: flex; justify-content: space-between; gap: 20px; margin-bottom: 30px;">
            <div style="flex: 1;">
                <h3 style="color: #00ff00; margin-top: 0; border-bottom: 1px solid #00ff0044; padding-bottom: 5px;">Venta (Más Baratas)</h3>
                {format_orders(market_data['top_global_sell_orders'], is_global=True)}
            </div>
            <div style="flex: 1;">
                <h3 style="color: #ff3333; margin-top: 0; border-bottom: 1px solid #ff333344; padding-bottom: 5px;">Compra (Más Caras)</h3>
                {format_orders(market_data['top_global_buy_orders'], is_global=True)}
            </div>
        </div>
    """
    
    specific_orders_html = f"""
        <hr style="border-color:#444; margin: 15px 0;">
        <h2 style="color: #1E90FF; text-align: center; margin-top: 30px; border-bottom: 2px solid #1E90FF44; padding-bottom: 5px;">
            Órdenes TOP 5 en **{source_market_name}** (Mercado de Rentabilidad)
        </h2>
        <div style="display: flex; justify-content: space-between; gap: 20px;">
            <div style="flex: 1;">
                <h3 style="color: #00ff00; margin-top: 0; border-bottom: 1px solid #00ff0044; padding-bottom: 5px;">Venta en {source_market_name}</h3>
                {format_orders(market_data['top_specific_sell_orders'], is_global=False)}
            </div>
            <div style="flex: 1;">
                <h3 style="color: #ff3333; margin-top: 0; border-bottom: 1px solid #ff333344; padding-bottom: 5px;">Compra en {source_market_name}</h3>
                {format_orders(market_data['top_specific_buy_orders'], is_global=False)}
            </div>
        </div>
    """
    
    volume_html = ""
    for days in VOLUME_TIMEFRAMES:
        volume = specific_volumes.get(days, 0)
        volume_formatted = "{:,.0f}".format(volume)
        volume_html += f"""
        <li style="margin-bottom: 8px; display: flex; justify-content: space-between; padding: 0 10px;">
            <span style="color: #ffeb3b; font-weight: bold;">Últimos {days} Días:</span>
            <span style="font-weight: bold; color: #4CAF50;">{volume_formatted} transacciones</span>
        </li>
        """

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
    
    summary_html = f"""
    <p style="font-size: 1.2em; font-weight: bold; color: #1E90FF; text-align: center;">📈 Resumen de Mercado para **{item_name}**</p>
    <hr style="border-color:#444; margin: 15px 0;">
    
    {global_orders_html}
    
    {specific_orders_html}

    {volume_section_html}
    """

    return jsonify({"summary_html": summary_html})


if __name__ == '__main__':
    app.run(debug=True)

application = app