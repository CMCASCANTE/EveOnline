import requests
import time
from datetime import datetime, timedelta
from flask import Flask, render_template, redirect, url_for, request

app = Flask(__name__)

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

# Límites de resultados
TOP_N_REGIONAL = 15
TOP_N_GLOBAL = 25
TOP_N_VOLUME_FILTER = 25

# =========================================================
# === FUNCIONES DE ANÁLISIS ESI (Copia de Script V32)
# =========================================================

def get_lp_store_offers_esi(corp_id):
    """ Obtiene TODAS las ofertas de la tienda LP de una corporación específica. """
    url = f"{ESI_BASE_URL}/loyalty/stores/{corp_id}/offers/?datasource=tranquility"
    try:
        # Añadir un User-Agent básico para ser un buen ciudadano de la ESI
        headers = {'User-Agent': 'EVE LP Market Analyzer / Contact: [Tu Nombre de Contacto en EVE]'}
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
        # Si falla, usamos el ID como nombre

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
                # Cálculo de rentabilidad basado en AVG (para la columna de clasificación original)
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
                    "type_id": type_id, # <--- CAMBIO CLAVE 1: AÑADIMOS EL TYPE_ID
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
        "Mercado", "AVG (30D)", "LOW (30D)", "ACTUAL", f"Volumen ({VOLUME_DAYS}D)", "IA ANÁLISIS", "Item (Cant.)" # <--- CAMBIO CLAVE 2: AÑADIMOS LA CABECERA
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
    # Nota: Si el análisis de ESI es muy largo, el navegador puede cancelar la conexión.
    # El redireccionamiento por JS en loading.html es la mejor práctica.
    return render_template('loading.html')

@app.route('/analyze')
def analyze():
    """ Ejecuta el análisis de la ESI y muestra los resultados. """
    
    # Esto asegura que el análisis solo se ejecute al llegar desde el /loading o al recargar /analyze
    # Evita ejecuciones accidentales al navegar.
    referrer = request.referrer if request.referrer else ''
    if not any(url_part in referrer for url_part in ['loading', 'analyze']):
         # Si llega directamente, forzamos el paso por la carga
         return redirect(url_for('loading')) 

    # Ejecutar el análisis (Esta llamada es la que tarda tiempo real)
    analysis_data = run_market_analysis()
    
    if "error" in analysis_data:
        # Pasa el mensaje de error al template
        return render_template('analysis.html', error=analysis_data['error'], current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S EVE Time"))
    
    return render_template('analysis.html', data=analysis_data)


if __name__ == '__main__':
    # Ejecuta la aplicación. Recuerda crear la carpeta 'templates' 
    # y los archivos HTML correspondientes.
    app.run(debug=True)


# app.py (Verificación de la variable de inicio)
# ...
application = app 
# ...
