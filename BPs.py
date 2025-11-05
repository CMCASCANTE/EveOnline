import requests
import csv
import io
import time
from datetime import datetime, timedelta

# --- CONSTANTES DE EVE ---
FUZZWORK_LPOFFERS_URL = "https://www.fuzzwork.co.uk/resources/lpOffers.txt"
ESI_BASE_URL = "https://esi.evetech.net/latest"
JITA_REGION_ID = 10000002        
FDU_CORP_ID = '1000181' # Federal Defense Union

# Definición de índices del archivo TSV (basado en el SDE estándar de Fuzzwork)
INDEX_CORPID = 1
INDEX_TYPEID = 2
INDEX_LPCOST = 4
INDEX_ISKCOST = 6
INDEX_AKCOST = 5 # akCost (Otro item requerido)
INDEX_QUANTITY = 3 # Cantidad del item (por si no es 1)

# --- UTILIDADES DE API ---

def get_30day_average_price(type_id, region_id=JITA_REGION_ID):
    """ Obtiene el precio de venta promedio de los últimos 30 días de la ESI. """
    url = f"{ESI_BASE_URL}/markets/{region_id}/history/?datasource=tranquility&type_id={type_id}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status() 
        history_data = response.json()
        
        # Filtrar datos de los últimos 30 días
        thirty_days_ago = datetime.now() - timedelta(days=30)
        
        prices = []
        for day in history_data:
            # La ESI devuelve la fecha como string (YYYY-MM-DD)
            date_obj = datetime.strptime(day['date'], '%Y-%m-%d')
            
            if date_obj >= thirty_days_ago:
                # Usamos (high + low) / 2 para un promedio diario aproximado o solo 'average' si existe (dependiendo de la ESI)
                # Para simplificar, usaremos el 'average' de la ESI o calcularemos (high+low)/2
                if 'average' in day:
                     prices.append(day['average'])
                else:
                    prices.append((day['highest'] + day['lowest']) / 2)

        if not prices:
            return 0, "No hay historial de precios en los últimos 30 días."
            
        # Devolver el promedio de los precios de los últimos 30 días
        avg_price = sum(prices) / len(prices)
        return avg_price, None

    except requests.exceptions.RequestException as e:
        return 0, f"Error API: {e}"
    except Exception as e:
        return 0, f"Error de cálculo/formato: {e}"

# --- FUNCIÓN DE CONSULTA Y PARSEO ---

def get_lp_store_offers():
    """ Descarga el TSV, filtra por FDU y por ítems sin materiales. """
    print(f"--- 🔎 Descargando y filtrando ofertas de FDU ({FDU_CORP_ID}) ---")
    
    try:
        response = requests.get(FUZZWORK_LPOFFERS_URL, timeout=30)
        response.raise_for_status()
        
        content = response.content.decode('utf-8')
        tsv_file = io.StringIO(content)
        reader = csv.reader(tsv_file, delimiter='\t')
        next(reader, None) # Saltar la primera fila
        
        filtered_offers = []

        for row in reader:
            if len(row) <= INDEX_ISKCOST:
                continue
                
            corp_id = row[INDEX_CORPID]
            
            # FILTRO 1: Solo Federal Defense Union
            if corp_id != FDU_CORP_ID:
                continue 

            # FILTRO 2: Solo ítems que NO requieren materiales (iskCost, akCost deben ser 0)
            try:
                isk_cost = int(row[INDEX_ISKCOST])
                ak_type_id = int(row[INDEX_AKCOST]) # El ID del item adicional requerido
                
                # akCost/akTypeID es el coste en items. Si es 0, no requiere materiales.
                if ak_type_id != 0:
                    continue 

                # Si llegamos aquí, solo necesita LP e ISK (ISK base no puede ser 0)
                if isk_cost == 0:
                    continue # Excluimos ofertas gratuitas o solo LP

                # Extraer todos los datos necesarios
                offer = {
                    "type_id": int(row[INDEX_TYPEID]),
                    "lp_cost": int(row[INDEX_LPCOST]),
                    "isk_cost": isk_cost,
                    "quantity": int(row[INDEX_QUANTITY]) # Cantidad de ítems por oferta
                }
                
                filtered_offers.append(offer)

            except ValueError:
                continue 

        print(f"✅ Ofertas filtradas: {len(filtered_offers)} (Solo LP+ISK, Corp {FDU_CORP_ID})")
        return filtered_offers, None

    except requests.exceptions.RequestException as e:
        return [], f"❌ ERROR al descargar/parsear TSV: {e}"

# --- FUNCIÓN PRINCIPAL ---

def main():
    print("--- 💸 RENTABILIDAD LP+ISK (Federal Defense Union, V13) 💸 ---")
    
    # Obtener la lista de ofertas filtradas
    offers, error = get_lp_store_offers()
    
    if error:
        print(f"\n{error}")
        return
        
    if not offers:
        print("\n⚠️ No se encontraron ofertas que solo requieran LP e ISK base para esta corporación.")
        return

    # Preparar el análisis
    results = []
    print(f"--- 📊 Analizando {len(offers)} ofertas con precio promedio de 30 días... ---")
    
    for offer in offers:
        type_id = offer['type_id']
        lp_cost = offer['lp_cost']
        isk_base_cost = offer['isk_cost']
        quantity = offer['quantity']
        
        # 1. Obtener el nombre del item (usando ESI /universe/names/)
        try:
            # Esta ruta es muy estable para obtener nombres a partir de IDs
            name_response = requests.post(f"{ESI_BASE_URL}/universe/names/?datasource=tranquility", 
                                          json=[type_id], timeout=5)
            name_response.raise_for_status()
            item_name = name_response.json()[0]['name']
        except Exception:
            item_name = f"ID: {type_id}"

        # 2. Obtener el precio promedio de 30 días
        avg_price_per_item, price_error = get_30day_average_price(type_id)
        
        # Solo calcular si encontramos un precio de reventa
        if avg_price_per_item > 0:
            
            # El costo y la ganancia deben ser por la cantidad de items en la oferta
            total_sell_value = avg_price_per_item * quantity
            total_cost = isk_base_cost
            
            isk_profit = total_sell_value - total_cost
            isk_per_lp = isk_profit / lp_cost if lp_cost > 0 else 0
            
            # Almacenar el resultado para ordenar
            results.append({
                "item_name": item_name,
                "isk_per_lp": isk_per_lp,
                "lp_cost": lp_cost,
                "isk_profit": isk_profit,
                "sell_price": avg_price_per_item,
                "quantity": quantity
            })
            
        time.sleep(0.1) # Pequeña pausa para evitar sobrecargar las APIs

    # Ordenar los resultados por la rentabilidad ISK/LP (descendente)
    results.sort(key=lambda x: x['isk_per_lp'], reverse=True)

    # 3. Mostrar Resultados Finales
    print("\n=======================================================================================")
    print(f"  TOP RENTABILIDAD LP+ISK (Federal Defense Union - Promedio 30 Días)")
    print("=======================================================================================")
    print(f"{'Rentabilidad (ISK/LP)':<25} | {'Ganancia Neta (ISK)':<25} | {'Item (Cantidad)':<40}")
    print("---------------------------------------------------------------------------------------")
    
    for r in results[:20]: # Mostrar los 20 mejores
        # Solo mostramos si la rentabilidad es positiva
        if r['isk_per_lp'] > 0:
            print(
                f"{r['isk_per_lp']:<25,.0f} | "
                f"{r['isk_profit']:<25,.0f} | "
                f"{r['item_name']} ({r['quantity']})"
            )
            
    print("=======================================================================================")

if __name__ == "__main__":
    main()
