import requests
import time
from datetime import datetime, timedelta
from flask import Flask, render_template, redirect, url_for, request, jsonify

app = Flask(__name__)

# --- CONSTANTES DE EVE ---
ESI_BASE_URL = "https://esi.evetech.net/latest"
FDU_CORP_ID = 1000181 # Federal Defense Union (ID numérico para la API)
TYPE_ID_ISK = 58 # Type ID para ISK
VOLUME_DAYS = 10 
# Filtros de Rentabilidad
MIN_ISK_PER_LP_FILTER_LOW = 1000 
MIN_ISK_PER_LP_FILTER_HIGH = 2000 
# --- CONSTANTES ADICIONALES ---
JITA_REGION_ID = 10000002 # ID de Jita

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

# =========================================================
# === FUNCIONES DE ANÁLISIS ESI 
# =========================================================

def get_lp_store_offers_esi(corp_id):
    """ Obtiene TODAS las ofertas de la tienda LP de una corporación específica. """
    url = f"{ESI_BASE_URL}/loyalty/stores/{corp_id}/offers/?datasource=tranquility"
    try:
        # Añadir un User-Agent básico para ser un buen ciudadano de la ESI
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
            # Solo si hay datos de precio, calcula el precio más bajo
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
        
        # Filtrar solo órdenes de venta activas (precio > 0)
        sell_prices = [order.get('price', float('inf')) for order in data if order.get('is_buy_order') == False and order.get('price', 0) > 0]
        
        # Encontrar el precio más bajo de venta (current_sell_price)
        current_sell_price = min(sell_prices) if sell_prices else 0
    except requests.exceptions.RequestException:
        pass 
        
    return avg_price, lowest_price, current_sell_price, volume_10d

def run_market_analysis():
    """ Ejecuta todo el proceso de análisis de ESI. """
    
    # 1. Obtener y filtrar ofertas LP
    all_offers, error = get_lp_store_offers_esi(FDU_CORP_ID)
    if error:
        return {"error": error}
        
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
        print(f"Error al obtener nombres: {e}") 

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
            
            # Obtener estadísticas específicas de la región
            avg_price, lowest_price, current_sell_price, volume_10d = get_market_stats(type_id, region_id)
            
            if avg_price > 0 or current_sell_price > 0:
                # Cálculo de rentabilidad basado en AVG
                price_to_use_avg = avg_price if avg_price > 0 else current_sell_price
                total_sell_value_avg = price_to_use_avg * quantity
                isk_profit_avg = total_sell_value_avg - isk_base_cost
                isk_per_lp_avg = isk_profit_avg / lp_cost if lp_cost > 0 else 0
                
                # Cálculo de rentabilidad basado en ACTUAL
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

            # Pequeña pausa para evitar el rate-limiting de la ESI (20ms)
            time.sleep(0.02) 

        # 4. Ordenar y guardar resultados regionales
        current_market_results.sort(key=lambda x: x['isk_per_lp'], reverse=True)
        market_results[market_name] = current_market_results[:TOP_N_REGIONAL]
    
    # 5. Procesar resultados globales (Función auxiliar para manejo de tablas globales)
    def process_global(results, sort_key, top_n, min_lp_filter):
        # 1. Filtrar
        filtered = [r for r in results if r['volume_10d'] > 0 and r['isk_per_lp'] >= min_lp_filter]
        
        # 2. Eliminar duplicados, manteniendo el mejor según el criterio de ordenación
        unique = {}
        for r in filtered:
            key = r['item_name']
            if key not in unique:
                unique[key] = r
            # Priorizar el mejor ISK/LP (si la clave de ordenación es ISK/LP)
            elif sort_key == 'isk_per_lp' and r['isk_per_lp'] > unique[key]['isk_per_lp']:
                unique[key] = r
            # Priorizar el mayor volumen (si la clave de ordenación es Volumen)
            elif sort_key == 'volume_10d' and r['volume_10d'] > unique[key]['volume_10d']:
                unique[key] = r
        
        final_list = list(unique.values())
        
        # 3. Ordenar
        final_list.sort(key=lambda x: x[sort_key], reverse=True)
        return final_list[:top_n]

    
    global_max_isk = process_global(global_results, 'isk_per_lp', TOP_N_GLOBAL, 0)
    global_liquidez_media = process_global(global_results, 'volume_10d', TOP_N_VOLUME_FILTER, MIN_ISK_PER_LP_FILTER_LOW)
    global_liquidez_alta = process_global(global_results, 'volume_10d', TOP_N_VOLUME_FILTER, MIN_ISK_PER_LP_FILTER_HIGH)
    
    # Datos de encabezado (para el HTML)
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
# === RUTAS DE FLASK
# =========================================================

@app.route('/')
def home():
    """ Muestra la página de inicio con el botón. """
    return render_template('index.html')

@app.route('/loading')
def loading():
    """ Muestra la animación de carga y redirige al análisis tras un breve retraso. """
    return render_template('loading.html')

@app.route('/analyze')
def analyze():
    """ Ejecuta el análisis de la ESI y muestra los resultados. """
    
    referrer = request.referrer if request.referrer else ''
    if not any(url_part in referrer for url_part in ['loading', 'analyze']):
         return redirect(url_for('loading')) 

    analysis_data = run_market_analysis()
    
    if "error" in analysis_data:
        return render_template('analysis.html', error=analysis_data['error'], current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S EVE Time"))
    
    return render_template('analysis.html', data=analysis_data)

# =========================================================
# === NUEVA FUNCIÓN ESI PARA ÓRDENES Y VOLUMEN (Global, para el modal)
# =========================================================

def get_global_market_summary(type_id, markets=MARKETS, top_n=5):
    """
    Obtiene las N órdenes de venta más baratas y N órdenes de compra más caras 
    de todos los mercados clave, y el volumen combinado de 10 días por mercado.
    """
    all_sell_orders = []
    all_buy_orders = []
    volume_by_market = {}
    
    # 1. Obtener Órdenes y Volumen por Mercado
    for market_name, region_id in markets.items():
        # --- Obtener Órdenes (Global Orders) ---
        orders_url = f"{ESI_BASE_URL}/markets/{region_id}/orders/?datasource=tranquility&type_id={type_id}"
        try:
            orders_response = requests.get(orders_url, timeout=15)
            orders_response.raise_for_status()
            all_orders = orders_response.json()
            
            for order in all_orders:
                # Filtrar solo órdenes activas y con precio > 0
                if order.get('price', 0) <= 0 or order.get('volume_remain', 0) <= 0:
                    continue
                    
                order_data = {
                    'price': order['price'],
                    'volume': order['volume_remain'],
                    'market': market_name,  # <--- AÑADIDO: Nombre del mercado
                    'location_id': order.get('location_id')
                }
                
                if order.get('is_buy_order'):
                    all_buy_orders.append(order_data)
                else:
                    all_sell_orders.append(order_data)

        except requests.exceptions.RequestException:
            # Continuar si un mercado falla
            pass 
        
        # --- Obtener Volumen (Combinado Buys/Sells) ---
        # NOTA IMPORTANTE: ESI History no separa volumen de compra y venta. 
        # Proporcionamos el TOTAL combinado de transacciones por mercado.
        volume_by_market[market_name] = {'total_volume_10d': 0}

        history_url = f"{ESI_BASE_URL}/markets/{region_id}/history/?datasource=tranquility&type_id={type_id}"
        try:
            history_response = requests.get(history_url, timeout=10)
            history_response.raise_for_status()
            history_data = history_response.json()
            
            ten_days_ago = datetime.now() - timedelta(days=VOLUME_DAYS)
            volume_10d = 0
            
            for day in history_data:
                date_obj = datetime.strptime(day['date'], '%Y-%m-%d')
                if date_obj >= ten_days_ago:
                    volume_10d += day['volume'] 
            
            volume_by_market[market_name]['total_volume_10d'] = volume_10d

        except requests.exceptions.RequestException:
             # Si falla el historial, el volumen es 0 para ese mercado
            pass 


    # 2. Filtrar y Ordenar Globalmente
    
    # Órdenes de Venta: Ordenar por PRECIO ASCENDENTE (más baratas)
    all_sell_orders.sort(key=lambda x: x['price'])
    top_sell_orders = all_sell_orders[:top_n]
    
    # Órdenes de Compra: Ordenar por PRECIO DESCENDENTE (más caras)
    all_buy_orders.sort(key=lambda x: x['price'], reverse=True)
    top_buy_orders = all_buy_orders[:top_n]

    return {
        'top_sell_orders': top_sell_orders,
        'top_buy_orders': top_buy_orders,
        'volume_by_market': volume_by_market,
        'error': None
    }


# =========================================================
# === RUTA API PARA EL MODAL (MODIFICADA)
# =========================================================

@app.route('/api/market-summary', methods=['POST'])
def market_summary_api():
    """ 
    Ruta para manejar la solicitud AJAX del modal. Obtiene órdenes TOP N globales 
    y el volumen combinado por mercado.
    """
    data = request.get_json()
    type_id = data.get('type_id')
    item_name = data.get('item_name')
    
    if not type_id:
        return jsonify({"summary_html": "<p>Error: ID de Item no proporcionado.</p>"}), 400

    try:
        type_id_int = int(type_id)
    except ValueError:
        return jsonify({"summary_html": "<p>Error: ID de Item inválido.</p>"}), 400

    # Llamada a la nueva función GLOBAL
    market_data = get_global_market_summary(type_id_int, markets=MARKETS, top_n=5)
    
    if market_data['error']:
        return jsonify({"summary_html": f"<p style='color: #ff3333;'>❌ Error ESI: {market_data['error']}</p>"}), 500

    
    # Función de utilidad para formatear órdenes (ahora incluye el mercado)
    def format_orders(orders):
        # Usamos tabla para la estructura de 3 columnas
        html = '<table style="width: 100%; border-collapse: collapse; font-size: 1.0em;">'
        html += '<tr style="border-bottom: 2px solid #555; font-size: 0.9em;">'
        html += '<th style="text-align: left; padding: 5px 0;">Precio</th>'
        html += '<th style="padding: 5px 0; text-align: center;">Volumen</th>'
        html += '<th style="text-align: right; padding: 5px 0;">Mercado</th>'
        html += '</tr>'
        
        if not orders:
             return "<p style='color: #888;'>No hay órdenes disponibles en los mercados clave.</p>"
             
        for order in orders:
            price_formatted = "{:,.2f}".format(order['price'])
            volume_formatted = "{:,.0f}".format(order['volume'])
            # Usamos verde para VENTA y rojo para COMPRA
            color = '#00ff00' if orders == market_data['top_sell_orders'] else '#ff3333'
            
            html += f'<tr style="border-bottom: 1px dashed #333;">'
            html += f'<td style="color: {color}; font-weight: bold; text-align: left; padding: 5px 0;">{price_formatted} ISK</td>'
            html += f'<td style="color: #eee; text-align: center; padding: 5px 0;">{volume_formatted} u.</td>'
            html += f'<td style="color: #ffeb3b; font-weight: bold; text-align: right; padding: 5px 0;">{order["market"]}</td>'
            html += f'</tr>'

        html += '</table>'
        return html

    # Estructura del Volumen por Mercado
    volume_html = ""
    for market, data in market_data['volume_by_market'].items():
        volume = data['total_volume_10d']
        volume_formatted = "{:,.0f}".format(volume)
        
        volume_html += f"""
        <li style="margin-bottom: 8px;">
            <span style="color: #ffeb3b; font-weight: bold; width: 100px; display: inline-block;">{market}:</span>
            <span style="font-weight: bold; color: #4CAF50;">{volume_formatted}</span> transacciones
        </li>
        """

    # Generar el HTML final para el modal
    summary_html = f"""
    <p style="font-size: 1.2em; font-weight: bold; color: #1E90FF; text-align: center;">📈 Órdenes Globales: {item_name}</p>
    <hr style="border-color:#444; margin: 15px 0;">

    <div style="display: flex; justify-content: space-between; gap: 20px;">
        <div style="flex: 1;">
            <h3 style="color: #00ff00; margin-top: 0; border-bottom: 1px solid #00ff0044; padding-bottom: 5px;">TOP 5 Venta (Más Baratas)</h3>
            {format_orders(market_data['top_sell_orders'])}
        </div>
        <div style="flex: 1;">
            <h3 style="color: #ff3333; margin-top: 0; border-bottom: 1px solid #ff333344; padding-bottom: 5px;">TOP 5 Compra (Más Caras)</h3>
            {format_orders(market_data['top_buy_orders'])}
        </div>
    </div>
    
    <hr style="border-color:#444; margin: 15px 0;">
    <p style="font-size: 1.1em; font-weight: bold; color: #ffeb3b; text-align: center;">📦 Volumen de Mercado por Región (Últimos {VOLUME_DAYS} Días)</p>
    <ul style="list-style: none; padding: 10px 0; text-align: left; font-size: 1.1em; max-width: 300px; margin: 10px auto;">
        {volume_html}
    </ul>
    
    <p style="font-size: 0.9em; color: #888; margin-top: 20px; text-align: center;">
        Fuente: ESI. El volumen es el **total combinado** de compras y ventas por mercado, ya que ESI no los separa.
    </p>
    """
    
    return jsonify({"summary_html": summary_html})


if __name__ == '__main__':
    app.run(debug=True)

application = app