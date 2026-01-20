import streamlit as st
import os, json, hashlib, time, base64, requests, msal
from datetime import datetime
from dotenv import load_dotenv
from openai import AzureOpenAI

try:
    from streamlit_javascript import st_javascript
    HAS_ST_JS = True
except ImportError:
    HAS_ST_JS = False
    st_javascript = lambda code: None

from dax_agent import create_dax_agent

load_dotenv()

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)

AAD_TENANT_ID = os.getenv("AAD_TENANT_ID")
AAD_CLIENT_ID = os.getenv("AAD_CLIENT_ID")
AAD_CLIENT_SECRET = os.getenv("AAD_CLIENT_SECRET")
POWERBI_WORKSPACE_ID = os.getenv("POWERBI_WORKSPACE_ID")
POWERBI_REPORT_ID = os.getenv("POWERBI_REPORT_ID")

agent_executor, system_prompt = create_dax_agent()

class InsightsCache:
    def __init__(self):
        self.cache = {}
        self.last_trigger = 0
        self.debounce_seconds = 1.5
    
    def get_context_hash(self, context):
        if not context: return None
        return hashlib.md5(json.dumps(context.get('filters', {}), sort_keys=True).encode()).hexdigest()
    
    def should_generate(self, context):
        if time.time() - self.last_trigger < self.debounce_seconds: return False
        context_hash = self.get_context_hash(context)
        return context_hash and context_hash not in self.cache
    
    def get_cached(self, context):
        return self.cache.get(self.get_context_hash(context))
    
    def store(self, context, insights):
        context_hash = self.get_context_hash(context)
        if context_hash:
            self.cache[context_hash] = {'insights': insights, 'timestamp': time.time()}
        self.last_trigger = time.time()

def get_powerbi_access_token():
    if not all([AAD_TENANT_ID, AAD_CLIENT_ID, AAD_CLIENT_SECRET]):
        return None, "Missing Azure AD credentials"
    try:
        app = msal.ConfidentialClientApplication(AAD_CLIENT_ID, authority=f"https://login.microsoftonline.com/{AAD_TENANT_ID}", client_credential=AAD_CLIENT_SECRET)
        token_response = app.acquire_token_for_client(scopes=["https://analysis.windows.net/powerbi/api/.default"])
        return (token_response["access_token"], None) if "access_token" in token_response else (None, f"Token error: {token_response.get('error_description', 'Unknown error')}")
    except Exception as e:
        return None, f"Failed to get Power BI token: {str(e)}"

def get_embed_token_for_report():
    access_token, err = get_powerbi_access_token()
    if err: return None, None, err
    if not all([POWERBI_WORKSPACE_ID, POWERBI_REPORT_ID]): return None, None, "Missing POWERBI_WORKSPACE_ID or POWERBI_REPORT_ID"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    try:
        report_url = f"https://api.powerbi.com/v1.0/myorg/groups/{POWERBI_WORKSPACE_ID}/reports/{POWERBI_REPORT_ID}"
        resp = requests.get(report_url, headers=headers, timeout=30)
        if resp.status_code != 200: return None, None, f"Error {resp.status_code}"
        embed_url = resp.json().get("embedUrl")
        resp = requests.post(f"{report_url}/GenerateToken", headers=headers, json={"accessLevel": "View"}, timeout=30)
        resp.raise_for_status()
        return embed_url, resp.json().get("token"), None
    except Exception as e:
        return None, None, f"Error: {str(e)}"

def generate_powerbi_embed_html(embed_url, embed_token, report_id):
    js_code = """
    var models=window['powerbi-client'].models,embedConfiguration={type:'report',id:'REPORT_ID',embedUrl:'EMBED_URL',accessToken:'EMBED_TOKEN',tokenType:models.TokenType.Embed,settings:{panes:{filters:{visible:true},pageNavigation:{visible:true}}},permissions:models.Permissions.All},reportContainer=document.getElementById('reportContainer'),report=powerbi.embed(reportContainer,embedConfiguration),lastFilterState='{}';
    function parseFilters(filters){var parsedFilters={};if(!filters||!Array.isArray(filters))return parsedFilters;filters.forEach(function(filter){try{if(!filter.target||!filter.target.column)return;var columnName=filter.target.column,tableName=filter.target.table||'Unknown',values=[];if(filter.values&&Array.isArray(filter.values)){values=filter.values}else if(filter.conditions&&Array.isArray(filter.conditions)){filter.conditions.forEach(function(cond){if(cond.value!==undefined)values.push(cond.value)})}if(values.length>0){parsedFilters[columnName]={table:tableName,values:values,type:filter.filterType||'basic'}}}catch(e){}});return parsedFilters}
    async function getAllFilters(){var allFilters={};try{var reportFilters=await report.getFilters();Object.assign(allFilters,parseFilters(reportFilters))}catch(e){}try{var page=await report.getActivePage();var pageFilters=await page.getFilters();Object.assign(allFilters,parseFilters(pageFilters))}catch(e){}try{var page=await report.getActivePage();var visuals=await page.getVisuals();for(var i=0;i<visuals.length;i++){try{if(visuals[i].type==='slicer'){var slicerState=await visuals[i].getSlicerState();if(slicerState&&slicerState.filters){Object.assign(allFilters,parseFilters(slicerState.filters))}}}catch(ve){}}}catch(e){}return allFilters}
    function storeFilters(parsedFilters){try{localStorage.setItem('pbi_filters',JSON.stringify(parsedFilters));localStorage.setItem('pbi_filters_ts',Date.now().toString())}catch(e){}}
    report.on('loaded',async function(){var allFilters=await getAllFilters();storeFilters(allFilters);lastFilterState=JSON.stringify(allFilters)});
    report.on('rendered',async function(){var allFilters=await getAllFilters();storeFilters(allFilters)});
    report.on('filtersApplied',async function(){var allFilters=await getAllFilters();storeFilters(allFilters);lastFilterState=JSON.stringify(allFilters)});
    report.on('dataSelected',async function(event){if(event.detail&&event.detail.dataPoints&&event.detail.dataPoints.length>0){var selectedData={};event.detail.dataPoints.forEach(function(dp){if(dp.identity){dp.identity.forEach(function(id){if(id.target&&id.target.column&&id.equals!==undefined){var col=id.target.column;if(!selectedData[col]){selectedData[col]={table:id.target.table||'Unknown',values:[],type:'selection'};}if(!selectedData[col].values.includes(id.equals)){selectedData[col].values.push(id.equals)}}})}});if(Object.keys(selectedData).length>0){storeFilters(selectedData);return}}setTimeout(async function(){var allFilters=await getAllFilters();storeFilters(allFilters)},300)});
    setInterval(async function(){var allFilters=await getAllFilters();var current=JSON.stringify(allFilters);if(current!==lastFilterState){storeFilters(allFilters);lastFilterState=current}},2000);
    """.replace('REPORT_ID', report_id).replace('EMBED_URL', embed_url).replace('EMBED_TOKEN', embed_token)
    return f"""<div id="reportContainer" style="width:100%; height:600px;"></div>
    <script src="https://cdn.jsdelivr.net/npm/powerbi-client@2.22.3/dist/powerbi.min.js"></script>
    <script>{js_code}</script>"""

def read_filters_from_localstorage(nonce=None):
    """Read filters from localStorage. Use nonce to bust st_javascript cache."""
    if not HAS_ST_JS: return None, None
    try:
        # Include nonce in the JS to force fresh execution each time
        nonce_str = f"/* nonce: {nonce} */" if nonce else ""
        js_code = f"{nonce_str}(function(){{ var f=localStorage.getItem('pbi_filters'); var t=localStorage.getItem('pbi_filters_ts'); return JSON.stringify({{filters:f,ts:t}}); }})()"
        result = st_javascript(js_code)
        if result and result not in ['null', 0, '0', None, '{}']:
            data = json.loads(result)
            filters_str = data.get('filters')
            ts_str = data.get('ts')
            filters = json.loads(filters_str) if filters_str and filters_str != 'null' else None
            ts = int(ts_str) if ts_str else None
            return filters, ts
    except: pass
    return None, None

def get_filters_from_url():
    try:
        params = st.query_params
        if 'pbi_filters' in params:
            return json.loads(base64.b64decode(params['pbi_filters']).decode('utf-8'))
    except: pass
    return {}

def generate_auto_insight_prompt(context):
    filters = context.get('filters', {}) if context else {}
    filter_parts = []
    for col_name, filter_data in filters.items():
        values = filter_data.get('values', [])
        if values:
            display_vals = []
            for v in values[:3]:
                if isinstance(v, str) and 'T' in v:
                    try: display_vals.append(datetime.fromisoformat(v.replace('Z', '+00:00')).strftime('%Y-%m-%d'))
                    except: display_vals.append(str(v))
                else: display_vals.append(str(v))
            filter_parts.append(f"{col_name}: {' to '.join(display_vals)}")
    filters_summary = "\n".join(filter_parts) if filter_parts else "No filters (showing all data)"
    return f"""Analyze the Power BI flight data with these filters applied:

**Active Filters:**
{filters_summary}

Provide a brief, actionable insight (2-3 sentences max) about:
1. Key metrics for this filtered view
2. Any notable patterns or trends
3. How this compares to the overall dataset if relevant

Be specific with numbers. Keep it concise."""

def get_auto_insights_from_agent(prompt, context):
    try:
        from dax_agent import execute_dax_query
        filters = context.get('filters', {}) if context else {}
        filter_conditions, filter_desc = [], []
        
        for col_name, filter_data in filters.items():
            table, values = filter_data.get('table', 'flights'), filter_data.get('values', [])
            if values and len(values) > 0:
                if col_name.upper() == 'DATE' and len(values) >= 2:
                    start_date = values[0][:10] if isinstance(values[0], str) else str(values[0])
                    end_date = values[1][:10] if isinstance(values[1], str) else str(values[1])
                    filter_conditions.append(f"'{table}'[{col_name}] >= DATE({start_date[:4]}, {int(start_date[5:7])}, {int(start_date[8:10])}) && '{table}'[{col_name}] <= DATE({end_date[:4]}, {int(end_date[5:7])}, {int(end_date[8:10])})")
                    filter_desc.append(f"{col_name}: {start_date} to {end_date}")
                elif len(values) == 1:
                    val = values[0]
                    filter_conditions.append(f"'{table}'[{col_name}] = \"{val}\"" if isinstance(val, str) else f"'{table}'[{col_name}] = {val}")
                    filter_desc.append(f"{col_name}: {val}")
                else:
                    vals_str = ', '.join([f'"{v}"' if isinstance(v, str) else str(v) for v in values])
                    filter_conditions.append(f"'{table}'[{col_name}] IN {{{vals_str}}}")
                    filter_desc.append(f"{col_name}: {', '.join(map(str, values[:3]))}")
        
        filter_clause = " && ".join(filter_conditions) if filter_conditions else None
        filtered_table = f"FILTER('flights', {filter_clause})" if filter_clause else "'flights'"
        stats = {}
        

        def extract_value(result):
            try:
                return list(result.get("results", [{}])[0].get("tables", [{}])[0].get("rows", [])[0].values())[0]
            except: return None
        

        result, err = execute_dax_query(f'EVALUATE ROW("Total", COUNTROWS({filtered_table}))')
        if not err and result:
            stats['total_flights'] = extract_value(result) or 0
        
 
        query = f'EVALUATE ROW("AvgDelay", CALCULATE(AVERAGE(\'flights\'[DEPARTURE_DELAY]), {filter_clause}))' if filter_clause else 'EVALUATE ROW("AvgDelay", AVERAGE(\'flights\'[DEPARTURE_DELAY]))'
        result, err = execute_dax_query(query)
        if not err and result:
            val = extract_value(result)
            stats['avg_delay'] = round(val, 1) if val else 0
        
    
        query = f'EVALUATE ROW("MaxDelay", CALCULATE(MAX(\'flights\'[DEPARTURE_DELAY]), {filter_clause}))' if filter_clause else 'EVALUATE ROW("MaxDelay", MAX(\'flights\'[DEPARTURE_DELAY]))'
        result, err = execute_dax_query(query)
        if not err and result:
            val = extract_value(result)
            stats['max_delay'] = round(val, 1) if val else 0

        query = f'EVALUATE ROW("MinDelay", CALCULATE(MIN(\'flights\'[DEPARTURE_DELAY]), {filter_clause}))' if filter_clause else 'EVALUATE ROW("MinDelay", MIN(\'flights\'[DEPARTURE_DELAY]))'
        result, err = execute_dax_query(query)
        if not err and result:
            val = extract_value(result)
            stats['min_delay'] = round(val, 1) if val else 0
        
   
        if filter_clause:
            query = f'EVALUATE ROW("OnTime", CALCULATE(COUNTROWS(FILTER(\'flights\', \'flights\'[DEPARTURE_DELAY] <= 0)), {filter_clause}))'
        else:
            query = 'EVALUATE ROW("OnTime", COUNTROWS(FILTER(\'flights\', \'flights\'[DEPARTURE_DELAY] <= 0)))'
        result, err = execute_dax_query(query)
        if not err and result:
            stats['on_time'] = extract_value(result) or 0
        

        if filter_clause:
            query = f'EVALUATE ROW("SlightDelay", CALCULATE(COUNTROWS(FILTER(\'flights\', \'flights\'[DEPARTURE_DELAY] > 0 && \'flights\'[DEPARTURE_DELAY] <= 15)), {filter_clause}))'
        else:
            query = 'EVALUATE ROW("SlightDelay", COUNTROWS(FILTER(\'flights\', \'flights\'[DEPARTURE_DELAY] > 0 && \'flights\'[DEPARTURE_DELAY] <= 15)))'
        result, err = execute_dax_query(query)
        if not err and result:
            stats['slight_delay'] = extract_value(result) or 0
        

        if filter_clause:
            query = f'EVALUATE ROW("ModerateDelay", CALCULATE(COUNTROWS(FILTER(\'flights\', \'flights\'[DEPARTURE_DELAY] > 15 && \'flights\'[DEPARTURE_DELAY] <= 60)), {filter_clause}))'
        else:
            query = 'EVALUATE ROW("ModerateDelay", COUNTROWS(FILTER(\'flights\', \'flights\'[DEPARTURE_DELAY] > 15 && \'flights\'[DEPARTURE_DELAY] <= 60)))'
        result, err = execute_dax_query(query)
        if not err and result:
            stats['moderate_delay'] = extract_value(result) or 0
        
      
        if filter_clause:
            query = f'EVALUATE ROW("SevereDelay", CALCULATE(COUNTROWS(FILTER(\'flights\', \'flights\'[DEPARTURE_DELAY] > 60)), {filter_clause}))'
        else:
            query = 'EVALUATE ROW("SevereDelay", COUNTROWS(FILTER(\'flights\', \'flights\'[DEPARTURE_DELAY] > 60)))'
        result, err = execute_dax_query(query)
        if not err and result:
            stats['severe_delay'] = extract_value(result) or 0
   
        query = f'EVALUATE ROW("Cancelled", CALCULATE(COUNTROWS(FILTER(\'flights\', \'flights\'[CANCELLED] = 1)), {filter_clause}))' if filter_clause else 'EVALUATE ROW("Cancelled", COUNTROWS(FILTER(\'flights\', \'flights\'[CANCELLED] = 1)))'
        result, err = execute_dax_query(query)
        if not err and result:
            stats['cancelled'] = extract_value(result) or 0
    
        query = f'EVALUATE ROW("Diverted", CALCULATE(COUNTROWS(FILTER(\'flights\', \'flights\'[DIVERTED] = 1)), {filter_clause}))' if filter_clause else 'EVALUATE ROW("Diverted", COUNTROWS(FILTER(\'flights\', \'flights\'[DIVERTED] = 1)))'
        result, err = execute_dax_query(query)
        if not err and result:
            stats['diverted'] = extract_value(result) or 0
        

        query = f'EVALUATE ROW("AvgArrDelay", CALCULATE(AVERAGE(\'flights\'[ARRIVAL_DELAY]), {filter_clause}))' if filter_clause else 'EVALUATE ROW("AvgArrDelay", AVERAGE(\'flights\'[ARRIVAL_DELAY]))'
        result, err = execute_dax_query(query)
        if not err and result:
            val = extract_value(result)
            stats['avg_arrival_delay'] = round(val, 1) if val else 0
        
        # Delay Type Breakdowns - sum of delay minutes by type
        delay_types = [
            ('AIR_SYSTEM_DELAY', 'air_system_delay'),
            ('AIRLINE_DELAY', 'airline_delay'),
            ('LATE_AIRCRAFT_DELAY', 'late_aircraft_delay'),
            ('SECURITY_DELAY', 'security_delay'),
            ('WEATHER_DELAY', 'weather_delay')
        ]
        
        for col_name, stat_key in delay_types:
            query = f"EVALUATE ROW(\"Total\", CALCULATE(SUM('flights'[{col_name}]), {filter_clause}))" if filter_clause else f"EVALUATE ROW(\"Total\", SUM('flights'[{col_name}]))"
            result, err = execute_dax_query(query)
            if not err and result:
                val = extract_value(result)
                stats[stat_key] = round(val, 1) if val else 0
            else:
                stats[stat_key] = 0
        
        # Calculate total delay minutes for percentage calculation
        total_delay_minutes = sum([
            stats.get('air_system_delay', 0),
            stats.get('airline_delay', 0),
            stats.get('late_aircraft_delay', 0),
            stats.get('security_delay', 0),
            stats.get('weather_delay', 0)
        ])
        
        # Calculate percentages for each delay type
        if total_delay_minutes > 0:
            stats['air_system_delay_pct'] = round((stats.get('air_system_delay', 0) / total_delay_minutes) * 100, 1)
            stats['airline_delay_pct'] = round((stats.get('airline_delay', 0) / total_delay_minutes) * 100, 1)
            stats['late_aircraft_delay_pct'] = round((stats.get('late_aircraft_delay', 0) / total_delay_minutes) * 100, 1)
            stats['security_delay_pct'] = round((stats.get('security_delay', 0) / total_delay_minutes) * 100, 1)
            stats['weather_delay_pct'] = round((stats.get('weather_delay', 0) / total_delay_minutes) * 100, 1)
        else:
            stats['air_system_delay_pct'] = 0
            stats['airline_delay_pct'] = 0
            stats['late_aircraft_delay_pct'] = 0
            stats['security_delay_pct'] = 0
            stats['weather_delay_pct'] = 0
        
        stats['total_delay_minutes'] = round(total_delay_minutes, 1)
   
        total = stats.get('total_flights', 0) or 1  # Avoid division by zero
        stats['on_time_pct'] = round((stats.get('on_time', 0) / total) * 100, 1)
        stats['slight_delay_pct'] = round((stats.get('slight_delay', 0) / total) * 100, 1)
        stats['moderate_delay_pct'] = round((stats.get('moderate_delay', 0) / total) * 100, 1)
        stats['severe_delay_pct'] = round((stats.get('severe_delay', 0) / total) * 100, 1)
        stats['cancellation_rate'] = round((stats.get('cancelled', 0) / total) * 100, 2)
        stats['diversion_rate'] = round((stats.get('diverted', 0) / total) * 100, 2)
        
        filter_text = ", ".join(filter_desc) if filter_desc else "No filters (full dataset)"
        
        stats_summary = f""" **Filtered Data Analysis** ({filter_text}):

**Overview:**
- Total Flights: {stats.get('total_flights', 0):,}
- On-Time Performance: {stats.get('on_time_pct', 0)}% ({stats.get('on_time', 0):,} flights)

**Delay Breakdown by Severity:**
- Average Departure Delay: {stats.get('avg_delay', 'N/A')} minutes
- Average Arrival Delay: {stats.get('avg_arrival_delay', 'N/A')} minutes
- Min Delay: {stats.get('min_delay', 'N/A')} min | Max Delay: {stats.get('max_delay', 'N/A')} min
- On-Time/Early (≤0 min): {stats.get('on_time', 0):,} ({stats.get('on_time_pct', 0)}%)
- Slight Delay (1-15 min): {stats.get('slight_delay', 0):,} ({stats.get('slight_delay_pct', 0)}%)
- Moderate Delay (16-60 min): {stats.get('moderate_delay', 0):,} ({stats.get('moderate_delay_pct', 0)}%)
- Severe Delay (>60 min): {stats.get('severe_delay', 0):,} ({stats.get('severe_delay_pct', 0)}%)

**Delay Breakdown by Type (Pie Chart Data):**
- Total Delay Minutes: {stats.get('total_delay_minutes', 0):,.1f} minutes
- Air System Delays: {stats.get('air_system_delay', 0):,.1f} min ({stats.get('air_system_delay_pct', 0)}%)
- Airline Delays: {stats.get('airline_delay', 0):,.1f} min ({stats.get('airline_delay_pct', 0)}%)
- Late Aircraft Delays: {stats.get('late_aircraft_delay', 0):,.1f} min ({stats.get('late_aircraft_delay_pct', 0)}%)
- Security Delays: {stats.get('security_delay', 0):,.1f} min ({stats.get('security_delay_pct', 0)}%)
- Weather Delays: {stats.get('weather_delay', 0):,.1f} min ({stats.get('weather_delay_pct', 0)}%)

**Cancellations & Diversions:**
- Cancelled Flights: {stats.get('cancelled', 0):,} ({stats.get('cancellation_rate', 0)}%)
- Diverted Flights: {stats.get('diverted', 0):,} ({stats.get('diversion_rate', 0)}%)"""
        
        response = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
            messages=[
                {"role": "system", "content": """You are a flight data analyst specializing in operational performance. Analyze the Power BI flight dashboard metrics.

The dashboard shows:
- **Total Flights Card**: Volume of flights analyzed
- **Avg Delay Card**: Average departure delay in minutes
- **Cancellation Rate Card**: Percentage of cancelled flights
- **Delay Breakdown Pie Chart**: Distribution of delay minutes by type (Air System Delays, Airline Delays, Late Aircraft Delays, Security Delays, Weather Delays) - use the "Delay Breakdown by Type" stats provided
- **Cancellations Over Time**: Monthly trend of cancellations showing seasonal patterns — you MUST interpret cancellation trends over time (e.g., increasing/decreasing, seasonality, spikes, and likely causes) when producing the Disruption Assessment.

Structure your response with exactly 4 numbered insights:

1. **Performance Summary** (1-2 sentences): Assess overall on-time rate and total flight volume. What does the average delay indicate about operational health?

2. **Operational Insight** (2-3 sentences): Analyze the Delay Breakdown by Type data. Which delay type dominates (Air System, Airline, Late Aircraft, Security, or Weather)? What percentage does it represent? What operational factors does this suggest (e.g., weather impact, airline operations, infrastructure issues, cascading delays from late aircraft)?

3. **Disruption Assessment** (1-2 sentences): Evaluate the cancellation rate AND explicitly interpret its trend over time — specify whether cancellations are rising, falling, seasonal, spiking, or stable, mention any timing or seasonal patterns, and compare to industry norms (typically 1-2%). Identify the month with the largest change in cancellations (increase or decrease), and report that month plus the change magnitude (absolute count or percentage) if available from the stats. Cite numbers and trend direction.

4. **Actionable Takeaway** (1 sentence): Based on the dominant delay type and cancellation trends, what is the single most impactful action to improve performance?

Always cite specific numbers and percentages from the stats provided, especially from the "Delay Breakdown by Type" section. Be analytical and operational-focused. When discussing cancellations, include a short sentence describing the historical/temporal trend and potential operational causes as inferred from the available metrics."""},
                {"role": "user", "content": f"{prompt}\n\n{stats_summary}"}
            ],
            max_tokens=400, temperature=0.3
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Could not generate insights: {str(e)}"

st.set_page_config(layout="wide", page_title="Power BI Insights", initial_sidebar_state="expanded")

for key, default in [('insights_cache', InsightsCache()), ('filter_context', None), ('insights_history', []), 
                     ('last_insight_time', 0), ('force_regenerate', False), ('selected_airline', "All"), 
                     ('selected_month', "All"), ('detected_filters', {})]:
    if key not in st.session_state:
        st.session_state[key] = default

missing_config = []
if not all([AAD_TENANT_ID, AAD_CLIENT_ID, AAD_CLIENT_SECRET]): missing_config.append("Azure AD credentials")
if not all([POWERBI_WORKSPACE_ID, POWERBI_REPORT_ID]): missing_config.append("Power BI IDs")
if missing_config:
    st.error(f"Missing: {', '.join(missing_config)}")
    st.stop()

left, right = st.columns([3, 1])

with left:
    st.markdown("### Power BI Report")
    embed_url, embed_token, err = get_embed_token_for_report()
    if err: st.error(err)
    else: st.components.v1.html(generate_powerbi_embed_html(embed_url, embed_token, POWERBI_REPORT_ID), height=620)

@st.fragment
def auto_insights_panel():
    st.markdown("###  Auto Insights")
    if 'gen_counter' not in st.session_state: st.session_state.gen_counter = 0
    
   
    sync_nonce = st.session_state.get('sync_nonce', 0)
    sync_phase = st.session_state.get('sync_phase', 0)
    

    current_ls_value, current_ts = None, None
    if HAS_ST_JS:

        use_nonce = sync_nonce if sync_phase > 0 else None
        current_ls_value, current_ts = read_filters_from_localstorage(nonce=use_nonce)
        if current_ls_value and len(current_ls_value) > 0:
            st.session_state.cached_pbi_filters = current_ls_value
            st.session_state.cached_pbi_ts = current_ts
    
 
    if sync_phase >= 3:
     
        st.session_state.sync_phase = 0
        if current_ls_value and len(current_ls_value) > 0:
            st.session_state.filter_context = {'filters': current_ls_value}
            st.session_state.applied_ts = current_ts
            st.session_state.gen_counter += 1
            st.toast(f" Synced {len(current_ls_value)} filter(s)!")
        else:
            # Fallback: use the cached values if direct read failed
            cached = st.session_state.get('cached_pbi_filters', {})
            if cached and len(cached) > 0:
                st.session_state.filter_context = {'filters': cached}
                st.session_state.gen_counter += 1
                st.toast(f" Synced {len(cached)} filter(s) from cache!")
            else:
                st.toast(" No filters detected - try again")
    elif sync_phase > 0:
     
        st.session_state.sync_phase = sync_phase + 1
 
        time.sleep(0.15)  
        st.rerun(scope="fragment")
    
    url_filters = get_filters_from_url()
    if url_filters and len(url_filters) > 0:
        stored_filters = (st.session_state.get('filter_context') or {}).get('filters', {})
        if url_filters != stored_filters:
            st.session_state.filter_context = {'filters': url_filters}
            st.session_state.gen_counter += 1
    
    if st.session_state.get('filter_context') is None:
        st.session_state.filter_context = {'filters': {}}
        st.session_state.gen_counter += 1
    
    st.divider()
    
    cached_pbi = st.session_state.get('cached_pbi_filters', {})
    cached_ts = st.session_state.get('cached_pbi_ts')
    if cached_pbi and len(cached_pbi) > 0:
        age_str = ""
        if cached_ts:
            age_seconds = (int(time.time() * 1000) - cached_ts) / 1000
            if age_seconds < 60:
                age_str = f" (updated {int(age_seconds)}s ago)"
            else:
                age_str = f" (updated {int(age_seconds/60)}m ago)"
        st.info(f"🔍 **Detected {len(cached_pbi)} Power BI filter(s)**{age_str} - Click Sync to apply")
    
    current_filters = (st.session_state.get('filter_context') or {}).get('filters', {})
    if current_filters and len(current_filters) > 0:
        st.success(f" **{len(current_filters)} filter(s) active**")
        for col_name, filter_data in current_filters.items():
            values = filter_data.get('values', [])
            display_vals = []
            for v in values[:3]:
                if isinstance(v, str) and 'T' in v:
                    try: display_vals.append(datetime.fromisoformat(v.replace('Z', '+00:00')).strftime('%Y-%m-%d'))
                    except: display_vals.append(str(v))
                else: display_vals.append(str(v))
            st.caption(f"• **{col_name}**: {' to '.join(display_vals)}")
    else: st.caption(" Analyzing full dataset")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button(" Sync Filters", use_container_width=True, type="primary", help="Read current filters from Power BI"):
        
            st.session_state.sync_phase = 1
            st.session_state.sync_nonce = int(time.time() * 1000)  
            st.rerun(scope="fragment")
    with col2:
        if st.button(" Regenerate", use_container_width=True, type="secondary"):
            st.session_state.gen_counter += 1
            st.session_state.pop('last_gen_counter', None)
            st.rerun(scope="fragment")
    
    st.divider()

    with st.container(border=True):
        st.markdown("####  Current Insights")
        current_context = st.session_state.get('filter_context')
        current_gen = st.session_state.get('gen_counter', 0)
        last_gen = st.session_state.get('last_gen_counter', -1)
        need_regenerate = (current_gen != last_gen)
        cached_insights = st.session_state.get('current_insights')
        
        if current_context and need_regenerate:
            with st.spinner(" Generating insights..."):
                try:
                    prompt = generate_auto_insight_prompt(current_context)
                    insights = get_auto_insights_from_agent(prompt, current_context)
                    st.session_state.current_insights = insights
                    st.session_state.last_gen_counter = current_gen
                    st.session_state.last_gen_time = time.time()
                    st.markdown(insights)
                    st.session_state.insights_history.insert(0, {'timestamp': time.time(), 'context': current_context, 'insights': insights})
                    st.session_state.insights_history = st.session_state.insights_history[:10]
                except Exception as e: st.error(f" {str(e)}")
        elif cached_insights:
            st.markdown(cached_insights)
            st.caption(f" Generated at {time.strftime('%H:%M:%S', time.localtime(st.session_state.get('last_gen_time', time.time())))}")
        else: st.info("Click 'Sync Filters' to load Power BI filters, or 'Regenerate' for fresh insights.")
    
    st.divider()
    
    if st.session_state.insights_history:
        with st.expander(" Recent Insights", expanded=False):
            for i, item in enumerate(st.session_state.insights_history[:5]):
                st.caption(f"**{time.strftime('%H:%M:%S', time.localtime(item['timestamp']))}**")
                st.markdown(item['insights'])
                if i < 4: st.divider()

@st.fragment
def ask_question_panel():
    st.markdown("###  Ask a Question")
    with st.form("question_form"):
        question = st.text_area("Your question:", placeholder="e.g., What's the delay trend?", height=80)
        submitted = st.form_submit_button("Ask", use_container_width=True)
        if submitted and question.strip():
            with st.spinner("Thinking..."):
                try:
                    from langchain_core.messages import HumanMessage, SystemMessage
                    messages = [SystemMessage(content=system_prompt), HumanMessage(content=question)]
                    response = agent_executor.invoke({"messages": messages})
                    if response and "messages" in response:
                        final_message = response["messages"][-1]
                        output = final_message.content if hasattr(final_message, 'content') else str(final_message)
                        st.markdown("**Answer:**")
                        st.markdown(output)
                except Exception as e: st.error(f"Error: {str(e)}")

with right:
    auto_insights_panel()
    st.divider()
    ask_question_panel()