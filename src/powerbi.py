import streamlit as st
import os
import requests
from dotenv import load_dotenv
from openai import AzureOpenAI
import msal
import json
import hashlib
import time
from datetime import datetime
from streamlit_javascript import st_javascript
from urllib.parse import parse_qs, quote, unquote
import base64

# Import agent setup from separate module
from dax_agent import create_dax_agent

load_dotenv()

# Azure OpenAI setup
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

# Create DAX agent and get system prompt
agent_executor, system_prompt = create_dax_agent()

# ============================================================================
# Insights Cache Class
# ============================================================================

class InsightsCache:
    def __init__(self):
        self.cache = {}
        self.last_trigger = 0
        self.debounce_seconds = 1.5  # Wait 1.5s before triggering
    
    def get_context_hash(self, context):
        """Create hash of filter state"""
        if not context:
            return None
        filter_str = json.dumps(context.get('filters', {}), sort_keys=True)
        return hashlib.md5(filter_str.encode()).hexdigest()
    
    def should_generate(self, context):
        """Check if we should generate new insights"""
        current_time = time.time()
        
        # Debounce: wait for user to finish adjusting filters
        if current_time - self.last_trigger < self.debounce_seconds:
            return False
        
        # Check cache
        context_hash = self.get_context_hash(context)
        if not context_hash:
            return False
            
        if context_hash in self.cache:
            # Return cached insights
            return False
        
        return True
    
    def get_cached(self, context):
        """Get cached insights if available"""
        context_hash = self.get_context_hash(context)
        return self.cache.get(context_hash)
    
    def store(self, context, insights):
        """Cache insights"""
        context_hash = self.get_context_hash(context)
        if context_hash:
            self.cache[context_hash] = {
                'insights': insights,
                'timestamp': time.time()
            }
        self.last_trigger = time.time()

# Initialize cache
if 'insights_cache' not in st.session_state:
    st.session_state.insights_cache = InsightsCache()

# ============================================================================
# Helper Functions
# ============================================================================

def get_powerbi_access_token():
    if not all([AAD_TENANT_ID, AAD_CLIENT_ID, AAD_CLIENT_SECRET]):
        return None, "Missing Azure AD credentials"
    try:
        app = msal.ConfidentialClientApplication(
            AAD_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{AAD_TENANT_ID}",
            client_credential=AAD_CLIENT_SECRET
        )
        token_response = app.acquire_token_for_client(scopes=["https://analysis.windows.net/powerbi/api/.default"])
        if "access_token" in token_response:
            return token_response["access_token"], None
        else:
            return None, f"Token error: {token_response.get('error_description', 'Unknown error')}"
    except Exception as e:
        return None, f"Failed to get Power BI token: {str(e)}"

def get_embed_token_for_report():
    access_token, err = get_powerbi_access_token()
    if err:
        return None, None, err
    if not all([POWERBI_WORKSPACE_ID, POWERBI_REPORT_ID]):
        return None, None, "Missing POWERBI_WORKSPACE_ID or POWERBI_REPORT_ID"
    
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    
    try:
        report_url = f"https://api.powerbi.com/v1.0/myorg/groups/{POWERBI_WORKSPACE_ID}/reports/{POWERBI_REPORT_ID}"
        resp = requests.get(report_url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return None, None, f"Error {resp.status_code}"
        embed_url = resp.json().get("embedUrl")
        
        token_url = f"{report_url}/GenerateToken"
        resp = requests.post(token_url, headers=headers, json={"accessLevel": "View"}, timeout=30)
        resp.raise_for_status()
        embed_token = resp.json().get("token")
        
        return embed_url, embed_token, None
    except Exception as e:
        return None, None, f"Error: {str(e)}"

def render_powerbi_embed_with_events(embed_url, embed_token, report_id):
    """Enhanced Power BI embed with WORKING filter change detection via localStorage"""
    return f"""
    <div id="reportContainer" style="width:100%; height:600px;"></div>
    <script src="https://cdn.jsdelivr.net/npm/powerbi-client@2.22.3/dist/powerbi.min.js"></script>
    <script>
        console.log('[PowerBI Embed] Initializing...');
        
        var models = window['powerbi-client'].models;
        var embedConfiguration = {{
            type: 'report',
            id: '{report_id}',
            embedUrl: '{embed_url}',
            accessToken: '{embed_token}',
            tokenType: models.TokenType.Embed,
            settings: {{
                panes: {{
                    filters: {{visible: true}},
                    pageNavigation: {{visible: true}}
                }}
            }},
            permissions: models.Permissions.All
        }};
        
        var reportContainer = document.getElementById('reportContainer');
        var report = powerbi.embed(reportContainer, embedConfiguration);
        
        var lastFilterState = '{{}}';
        
        function parseFilters(filters) {{
            var parsedFilters = {{}};
            if (filters && Array.isArray(filters)) {{
                filters.forEach(function(filter) {{
                    try {{
                        if (filter.target && filter.target.column) {{
                            var columnName = filter.target.column;
                            var tableName = filter.target.table || 'Unknown';
                            
                            var values = [];
                            if (filter.values && Array.isArray(filter.values)) {{
                                values = filter.values;
                            }} else if (filter.conditions && Array.isArray(filter.conditions)) {{
                                filter.conditions.forEach(function(cond) {{
                                    if (cond.value !== undefined) {{
                                        values.push(cond.value);
                                    }}
                                }});
                            }}
                            
                            if (values.length > 0) {{
                                parsedFilters[columnName] = {{
                                    table: tableName,
                                    values: values,
                                    type: filter.filterType || 'basic'
                                }};
                            }}
                        }}
                    }} catch (e) {{
                        console.log('[PowerBI Embed] Filter parse error:', e);
                    }}
                }});
            }}
            return parsedFilters;
        }}
        
        function storeFilters(parsedFilters) {{
            try {{
                // Store in localStorage so parent window can access
                localStorage.setItem('powerbi_filters', JSON.stringify(parsedFilters));
                localStorage.setItem('powerbi_filters_timestamp', Date.now().toString());
                console.log('[PowerBI Embed] Stored filters in localStorage:', parsedFilters);
                
                // Also post to parent window
                window.parent.postMessage({{
                    type: 'powerbi-filters',
                    filters: parsedFilters,
                    timestamp: Date.now()
                }}, '*');
            }} catch (e) {{
                console.log('[PowerBI Embed] Storage error:', e);
            }}
        }}
        
        report.on('loaded', async function() {{
            console.log('[PowerBI Embed] Report loaded successfully');
            
            // Get initial filters
            try {{
                var page = await report.getActivePage();
                var filters = await page.getFilters();
                var parsedFilters = parseFilters(filters);
                storeFilters(parsedFilters);
                lastFilterState = JSON.stringify(parsedFilters);
                console.log('[PowerBI Embed] Initial filters:', parsedFilters);
            }} catch (e) {{
                console.log('[PowerBI Embed] Could not get initial filters:', e);
            }}
        }});
        
        report.on('error', function(event) {{
            console.error('[PowerBI Embed] Error:', event.detail);
        }});
        
        // Main filter detection - triggers whenever filters are applied
        report.on('filtersApplied', async function(event) {{
            try {{
                console.log('[PowerBI Embed] filtersApplied event triggered');
                
                var page = await report.getActivePage();
                var filters = await page.getFilters();
                var parsedFilters = parseFilters(filters);
                
                var currentFilterState = JSON.stringify(parsedFilters);
                
                // Always store, even if same (timestamp will update)
                storeFilters(parsedFilters);
                lastFilterState = currentFilterState;
                console.log('[PowerBI Embed] Filters updated:', parsedFilters);
            }} catch (error) {{
                console.error('[PowerBI Embed] Error capturing filters:', error);
            }}
        }});
        
        // Also listen for data selected events (clicking on visuals)
        report.on('dataSelected', async function(event) {{
            console.log('[PowerBI Embed] dataSelected event:', event.detail);
            try {{
                // Small delay to let Power BI update filters
                setTimeout(async function() {{
                    var page = await report.getActivePage();
                    var filters = await page.getFilters();
                    var parsedFilters = parseFilters(filters);
                    storeFilters(parsedFilters);
                }}, 500);
            }} catch (e) {{
                console.log('[PowerBI Embed] dataSelected filter capture error:', e);
            }}
        }});
        
        // Periodic sync every 3 seconds as backup
        setInterval(async function() {{
            try {{
                var page = await report.getActivePage();
                var filters = await page.getFilters();
                var parsedFilters = parseFilters(filters);
                var currentFilterState = JSON.stringify(parsedFilters);
                
                if (currentFilterState !== lastFilterState) {{
                    console.log('[PowerBI Embed] Periodic sync detected change');
                    storeFilters(parsedFilters);
                    lastFilterState = currentFilterState;
                }}
            }} catch (e) {{
                // Silent
            }}
        }}, 3000);
        
        console.log('[PowerBI Embed] Initialization complete');
    </script>
    """

def format_filter_summary(context):
    """Format filter context into readable summary"""
    if not context or 'filters' not in context:
        return "No active filters"
    
    filters = context.get('filters', [])
    if not filters:
        return "No active filters"
    
    summary_parts = []
    for f in filters:
        if isinstance(f, dict):
            target = f.get('target', {})
            table = target.get('table', 'Unknown')
            column = target.get('column', 'Unknown')
            
            # Try to get filter values
            if 'values' in f:
                values = f['values']
                if isinstance(values, list) and len(values) > 0:
                    summary_parts.append(f"**{column}**: {', '.join(map(str, values[:3]))}")
            elif 'operator' in f:
                operator = f.get('operator', '')
                summary_parts.append(f"**{column}**: {operator} filter applied")
    
    return "\n".join(summary_parts) if summary_parts else "Filters applied (details unavailable)"

def generate_auto_insight_prompt(context):
    """Generate contextual prompt for auto-insights"""
    # Get actual filter values
    airline = context.get('airline', 'All')
    month = context.get('month', 'All')
    
    filter_parts = []
    if airline != "All":
        filter_parts.append(f"Airline: {airline}")
    if month != "All":
        filter_parts.append(f"Month: {month}")
    
    filters_summary = "\n".join(filter_parts) if filter_parts else "No filters (showing all data)"
    
    prompt = f"""Analyze the Power BI flight data with these filters applied:

**Active Filters:**
{filters_summary}

Provide a brief, actionable insight (2-3 sentences max) about:
1. Key metrics for this filtered view
2. Any notable patterns or trends
3. How this compares to the overall dataset if relevant

Be specific with numbers. Keep it concise."""
    
    return prompt

def get_auto_insights_from_agent(prompt, context):
    """Get FAST insights using direct Azure OpenAI call with FILTERED stats"""
    try:
        from dax_agent import execute_dax_query
        
        # Get current filter selections
        airline_filter = st.session_state.get('selected_airline', 'All')
        month_filter = st.session_state.get('selected_month', 'All')
        
        # Build filter clause for DAX
        filter_conditions = []
        if airline_filter != "All":
            filter_conditions.append(f"'flights'[AIRLINE] = \"{airline_filter}\"")
        if month_filter != "All":
            filter_conditions.append(f"'flights'[MONTH] = {month_filter}")
        
        # Create filtered table expression
        if filter_conditions:
            filter_clause = " && ".join(filter_conditions)
            filtered_table = f"FILTER('flights', {filter_clause})"
        else:
            filtered_table = "'flights'"
        
        stats = {}
        
        # Total flights (filtered)
        query = f'EVALUATE ROW("Total", COUNTROWS({filtered_table}))'
        result, err = execute_dax_query(query)
        if not err and result:
            try:
                rows = result.get("results", [{}])[0].get("tables", [{}])[0].get("rows", [])
                if rows:
                    stats['total_flights'] = list(rows[0].values())[0]
            except:
                pass
        
        # Average delay (filtered)
        if filter_conditions:
            query = f'EVALUATE ROW("AvgDelay", CALCULATE(AVERAGE(\'flights\'[DEPARTURE_DELAY]), {filter_clause}))'
        else:
            query = 'EVALUATE ROW("AvgDelay", AVERAGE(\'flights\'[DEPARTURE_DELAY]))'
        result, err = execute_dax_query(query)
        if not err and result:
            try:
                rows = result.get("results", [{}])[0].get("tables", [{}])[0].get("rows", [])
                if rows:
                    val = list(rows[0].values())[0]
                    stats['avg_delay'] = round(val, 1) if val else 0
            except:
                pass
        
        # Cancellation count (filtered)
        if filter_conditions:
            query = f'EVALUATE ROW("Cancelled", CALCULATE(COUNTROWS(FILTER(\'flights\', \'flights\'[CANCELLED] = 1)), {filter_clause}))'
        else:
            query = 'EVALUATE ROW("Cancelled", COUNTROWS(FILTER(\'flights\', \'flights\'[CANCELLED] = 1)))'
        result, err = execute_dax_query(query)
        if not err and result:
            try:
                rows = result.get("results", [{}])[0].get("tables", [{}])[0].get("rows", [])
                if rows:
                    stats['cancelled'] = list(rows[0].values())[0]
            except:
                pass
        
        # Build filter description
        filter_desc = []
        if airline_filter != "All":
            filter_desc.append(f"Airline: {airline_filter}")
        if month_filter != "All":
            filter_desc.append(f"Month: {month_filter}")
        filter_text = ", ".join(filter_desc) if filter_desc else "No filters (full dataset)"
        
        # Build stats summary
        total = stats.get('total_flights', 0)
        stats_summary = f"""Filtered Data Stats ({filter_text}):
- Total Flights: {total:,}
- Average Delay: {stats.get('avg_delay', 'N/A')} minutes
- Cancelled Flights: {stats.get('cancelled', 'N/A'):,}"""
        
        # Use direct Azure OpenAI call
        response = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
            messages=[
                {"role": "system", "content": "You are a flight data analyst. Provide brief, insightful summaries (2-3 sentences max). Be specific with numbers. Mention the filter applied."},
                {"role": "user", "content": f"{prompt}\n\n{stats_summary}"}
            ],
            max_tokens=150,
            temperature=0.3
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        return f"Could not generate insights: {str(e)}"

# ============================================================================
# UI Components
# ============================================================================

st.set_page_config(layout="wide", page_title="Power BI Insights", initial_sidebar_state="expanded")

# Initialize session state
if 'filter_context' not in st.session_state:
    st.session_state.filter_context = None
if 'insights_history' not in st.session_state:
    st.session_state.insights_history = []
if 'last_insight_time' not in st.session_state:
    st.session_state.last_insight_time = 0
if 'force_regenerate' not in st.session_state:
    st.session_state.force_regenerate = False
if 'selected_airline' not in st.session_state:
    st.session_state.selected_airline = "All"
if 'selected_month' not in st.session_state:
    st.session_state.selected_month = "All"

# Check for missing config
missing_config = []
if not all([AAD_TENANT_ID, AAD_CLIENT_ID, AAD_CLIENT_SECRET]):
    missing_config.append("Azure AD credentials")
if not all([POWERBI_WORKSPACE_ID, POWERBI_REPORT_ID]):
    missing_config.append("Power BI IDs")

if missing_config:
    st.error(f"Missing: {', '.join(missing_config)}")
    st.stop()

# Create two columns: Power BI (left) and Insights (right)
left, right = st.columns([3, 1])

with left:
    st.markdown("### Power BI Report")
    embed_url, embed_token, err = get_embed_token_for_report()
    if err:
        st.error(err)
    else:
        # Create a combined HTML that stores filters in parent window's sessionStorage
        combined_html = f"""
        <div id="reportContainer" style="width:100%; height:580px;"></div>
        <div id="filterIndicator" style="padding: 5px 10px; background: #f0f2f6; font-size: 12px; color: #666; border-top: 1px solid #ddd;">Loading filters...</div>
        <script src="https://cdn.jsdelivr.net/npm/powerbi-client@2.22.3/dist/powerbi.min.js"></script>
        <script>
            console.log('[PowerBI Embed] Initializing...');
            
            var models = window['powerbi-client'].models;
            var embedConfiguration = {{
                type: 'report',
                id: '{POWERBI_REPORT_ID}',
                embedUrl: '{embed_url}',
                accessToken: '{embed_token}',
                tokenType: models.TokenType.Embed,
                settings: {{
                    panes: {{
                        filters: {{visible: true}},
                        pageNavigation: {{visible: true}}
                    }}
                }},
                permissions: models.Permissions.All
            }};
            
            var reportContainer = document.getElementById('reportContainer');
            var report = powerbi.embed(reportContainer, embedConfiguration);
            
            var lastFilterState = '{{}}';
            
            function parseFilters(filters) {{
                var parsedFilters = {{}};
                if (filters && Array.isArray(filters)) {{
                    filters.forEach(function(filter) {{
                        try {{
                            if (filter.target && filter.target.column) {{
                                var columnName = filter.target.column;
                                var tableName = filter.target.table || 'Unknown';
                                var values = [];
                                
                                if (filter.values && Array.isArray(filter.values)) {{
                                    values = filter.values;
                                }} else if (filter.conditions && Array.isArray(filter.conditions)) {{
                                    filter.conditions.forEach(function(cond) {{
                                        if (cond.value !== undefined) values.push(cond.value);
                                    }});
                                }}
                                
                                if (values.length > 0) {{
                                    parsedFilters[columnName] = {{
                                        table: tableName,
                                        values: values,
                                        type: filter.filterType || 'basic'
                                    }};
                                }}
                            }}
                        }} catch (e) {{}}
                    }});
                }}
                return parsedFilters;
            }}
            
            async function getAllFilters() {{
                var allFilters = {{}};
                
                try {{
                    var reportFilters = await report.getFilters();
                    Object.assign(allFilters, parseFilters(reportFilters));
                }} catch (e) {{}}
                
                try {{
                    var page = await report.getActivePage();
                    var pageFilters = await page.getFilters();
                    Object.assign(allFilters, parseFilters(pageFilters));
                }} catch (e) {{}}
                
                try {{
                    var page = await report.getActivePage();
                    var visuals = await page.getVisuals();
                    
                    for (var i = 0; i < visuals.length; i++) {{
                        var visual = visuals[i];
                        try {{
                            if (visual.type === 'slicer') {{
                                var slicerState = await visual.getSlicerState();
                                if (slicerState && slicerState.filters) {{
                                    Object.assign(allFilters, parseFilters(slicerState.filters));
                                }}
                            }}
                        }} catch (ve) {{}}
                    }}
                }} catch (e) {{}}
                
                return allFilters;
            }}
            
            function storeFilters(parsedFilters) {{
                try {{
                    var filterJson = JSON.stringify(parsedFilters);
                    // Store in parent window's sessionStorage (shared with Streamlit)
                    window.parent.sessionStorage.setItem('powerbi_filters', filterJson);
                    window.parent.sessionStorage.setItem('powerbi_filters_ts', Date.now().toString());
                    
                    // Also store in own storage for reader component
                    sessionStorage.setItem('powerbi_filters', filterJson);
                    localStorage.setItem('powerbi_filters', filterJson);
                    
                    // Log clearly for easy copying
                    console.log('[PowerBI Embed] ========== CURRENT FILTERS ==========');
                    console.log('[PowerBI Embed] Copy this JSON:', filterJson);
                    console.log('[PowerBI Embed] =====================================');
                    
                    // Update visible indicator if exists
                    var indicator = document.getElementById('filterIndicator');
                    if (indicator) {{
                        var count = Object.keys(parsedFilters).length;
                        if (count > 0) {{
                            indicator.innerHTML = '🎯 ' + count + ' filter(s) active';
                            indicator.style.color = 'green';
                        }} else {{
                            indicator.innerHTML = 'No filters';
                            indicator.style.color = '#666';
                        }}
                    }}
                }} catch (e) {{
                    console.log('[PowerBI Embed] Storage error:', e);
                }}
            }}
            
            report.on('loaded', async function() {{
                console.log('[PowerBI Embed] Report loaded');
                var allFilters = await getAllFilters();
                storeFilters(allFilters);
                lastFilterState = JSON.stringify(allFilters);
            }});
            
            report.on('rendered', async function() {{
                console.log('[PowerBI Embed] Report rendered');
                var allFilters = await getAllFilters();
                storeFilters(allFilters);
            }});
            
            report.on('filtersApplied', async function() {{
                console.log('[PowerBI Embed] Filters applied');
                var allFilters = await getAllFilters();
                storeFilters(allFilters);
                lastFilterState = JSON.stringify(allFilters);
            }});
            
            report.on('dataSelected', async function(event) {{
                console.log('[PowerBI Embed] Data selected:', event.detail);
                
                // Extract selection from dataPoints
                if (event.detail && event.detail.dataPoints && event.detail.dataPoints.length > 0) {{
                    var selectedData = {{}};
                    event.detail.dataPoints.forEach(function(dp) {{
                        if (dp.identity) {{
                            dp.identity.forEach(function(id) {{
                                if (id.target && id.target.column && id.equals !== undefined) {{
                                    var col = id.target.column;
                                    if (!selectedData[col]) {{
                                        selectedData[col] = {{
                                            table: id.target.table || 'Unknown',
                                            values: [],
                                            type: 'selection'
                                        }};
                                    }}
                                    if (!selectedData[col].values.includes(id.equals)) {{
                                        selectedData[col].values.push(id.equals);
                                    }}
                                }}
                            }});
                        }}
                    }});
                    
                    if (Object.keys(selectedData).length > 0) {{
                        storeFilters(selectedData);
                        return;
                    }}
                }}
                
                // Fallback
                setTimeout(async function() {{
                    var allFilters = await getAllFilters();
                    storeFilters(allFilters);
                }}, 300);
            }});
            
            // Sync every 2 seconds
            setInterval(async function() {{
                var allFilters = await getAllFilters();
                var current = JSON.stringify(allFilters);
                if (current !== lastFilterState) {{
                    storeFilters(allFilters);
                    lastFilterState = current;
                }}
            }}, 2000);
            
            console.log('[PowerBI Embed] Ready');
        </script>
        """
        
        st.components.v1.html(combined_html, height=620)
        
        # Display current filters stored (read via a simple approach)
        st.caption("🔍 Filters sync automatically when changed in Power BI")

# Global variable to store filters (set by the reader component)
if 'detected_filters' not in st.session_state:
    st.session_state.detected_filters = {}

def create_filter_reader_component():
    """Create a component that reads filters and updates URL params for Python to read"""
    reader_html = """
    <div id="filterReader" style="padding: 10px; background: #e8f4e8; border-radius: 5px; font-family: sans-serif; border: 1px solid #4CAF50;">
        <div id="filterStatus" style="color: #333; font-size: 13px; font-weight: 500;">🔍 Reading filters...</div>
        <button id="applyBtn" onclick="applyFilters()" style="margin-top: 8px; padding: 8px 16px; background: #4CAF50; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; display: none;">
            ✅ Apply These Filters
        </button>
    </div>
    <script>
        var currentFilters = '{}';
        
        function readFilters() {
            try {
                var filters = window.parent.sessionStorage.getItem('powerbi_filters');
                
                if (filters && filters !== '{}' && filters !== 'null') {
                    currentFilters = filters;
                    var parsed = JSON.parse(filters);
                    var filterCount = Object.keys(parsed).length;
                    
                    if (filterCount > 0) {
                        var summary = [];
                        for (var key in parsed) {
                            var vals = parsed[key].values || [];
                            // Format dates nicely
                            var displayVals = vals.slice(0, 2).map(function(v) {
                                if (typeof v === 'string' && v.includes('T')) {
                                    return new Date(v).toLocaleDateString();
                                }
                                return v;
                            });
                            summary.push('<b>' + key + '</b>: ' + displayVals.join(' - '));
                        }
                        document.getElementById('filterStatus').innerHTML = '✅ <b>' + filterCount + ' filter(s) detected:</b><br>' + summary.join('<br>');
                        document.getElementById('applyBtn').style.display = 'block';
                    } else {
                        document.getElementById('filterStatus').innerHTML = '📊 No filters - will analyze full dataset';
                        document.getElementById('applyBtn').style.display = 'block';
                        document.getElementById('applyBtn').innerText = '🚀 Analyze Full Dataset';
                    }
                } else {
                    document.getElementById('filterStatus').innerHTML = '⏳ Waiting for Power BI filters...';
                    document.getElementById('applyBtn').style.display = 'none';
                }
            } catch (e) {
                console.error('[FilterReader] Error:', e);
            }
        }
        
        function applyFilters() {
            try {
                // Encode filters as base64 to avoid URL encoding issues
                var encoded = btoa(currentFilters);
                
                // Update URL with filters parameter
                var url = new URL(window.top.location.href);
                url.searchParams.set('pbi_filters', encoded);
                url.searchParams.set('pbi_ts', Date.now().toString());
                
                // Navigate to trigger Streamlit refresh with new params
                window.top.location.href = url.toString();
            } catch (e) {
                console.error('[FilterReader] Apply error:', e);
                alert('Error applying filters. Please try the manual input method.');
            }
        }
        
        // Read immediately and every 2 seconds
        readFilters();
        setInterval(readFilters, 2000);
    </script>
    """
    return reader_html


def get_filters_from_url():
    """Read filters from URL query parameters"""
    try:
        params = st.query_params
        if 'pbi_filters' in params:
            encoded = params['pbi_filters']
            decoded = base64.b64decode(encoded).decode('utf-8')
            return json.loads(decoded)
    except Exception as e:
        pass
    return {}

# ============================================================================
# Auto-Insights Panel (Right Sidebar)
# ============================================================================

def get_powerbi_filters_via_js():
    """Read filters from parent window's sessionStorage (set by Power BI iframe)"""
    js_code = """
    (function() {
        try {
            // Power BI iframe stores in parent's sessionStorage
            // We need to access window.parent.sessionStorage (or top)
            var storage = null;
            
            // Try parent window first
            try {
                storage = window.parent.sessionStorage;
            } catch (e) {
                // If blocked, try top
                try {
                    storage = window.top.sessionStorage;
                } catch (e2) {
                    // Fall back to own sessionStorage
                    storage = sessionStorage;
                }
            }
            
            var filters = storage.getItem('powerbi_filters');
            console.log('[GetFilters] Raw value from storage:', filters);
            
            if (filters && filters !== '{}' && filters !== 'null' && filters !== null) {
                console.log('[GetFilters] Found filters:', filters);
                return filters;
            }
            
            console.log('[GetFilters] No filters found in any storage');
            return '{}';
        } catch (e) {
            console.error('[GetFilters] Error:', e.message);
            return '{}';
        }
    })()
    """
    try:
        result = st_javascript(js_code)
        st.write(f"DEBUG: Raw JS result = {result}")  # Temporary debug
        if result and result != '{}' and result != 0 and result != '0':
            parsed = json.loads(result)
            if parsed and len(parsed) > 0:
                return parsed
    except Exception as e:
        st.write(f"DEBUG: Exception = {e}")  # Temporary debug
        pass
    return {}

def auto_insights_panel():
    st.markdown("### 📊 Auto Insights")
    
    # Check for filters from URL (set by the filter reader component)
    url_filters = get_filters_from_url()
    if url_filters and len(url_filters) > 0:
        # We have filters from URL - store them and trigger regeneration
        if st.session_state.get('last_url_filters') != url_filters:
            st.session_state.filter_context = {'filters': url_filters}
            st.session_state.last_url_filters = url_filters
            st.session_state.force_regenerate = True
            
            # Extract specific filter values
            for col_name, filter_data in url_filters.items():
                values = filter_data.get('values', [])
                if 'DATE' in col_name.upper() and len(values) >= 2:
                    st.session_state.selected_date_range = values
    
    st.divider()
    
    # Show the filter reader component
    st.caption("📡 **Filter Detection:**")
    reader_html = create_filter_reader_component()
    st.components.v1.html(reader_html, height=100)
    
    st.divider()
    
    # Show current filter status
    current_filters = st.session_state.get('filter_context', {}).get('filters', {})
    if current_filters and len(current_filters) > 0:
        st.success(f"🎯 **Active: {len(current_filters)} filter(s)**")
        for col_name, filter_data in current_filters.items():
            values = filter_data.get('values', [])
            # Format dates nicely
            display_vals = []
            for v in values[:3]:
                if isinstance(v, str) and 'T' in v:
                    try:
                        dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
                        display_vals.append(dt.strftime('%Y-%m-%d'))
                    except:
                        display_vals.append(str(v))
                else:
                    display_vals.append(str(v))
            st.caption(f"• **{col_name}**: {' to '.join(display_vals)}")
    else:
        st.info("📊 No filters - will analyze full dataset")
    
    # Manual override option
    with st.expander("⚙️ Manual Filter Override", expanded=False):
        manual_json = st.text_area(
            "Paste filter JSON (from console):",
            height=80,
            key="manual_json_input"
        )
        if st.button("Apply Manual Filters", key="apply_manual_btn"):
            try:
                parsed = json.loads(manual_json) if manual_json else {}
                st.session_state.filter_context = {'filters': parsed}
                st.session_state.force_regenerate = True
                st.success("✅ Manual filters applied!")
                st.rerun()
            except json.JSONDecodeError:
                st.error("Invalid JSON format")
    
    st.divider()
    
    # Main insights container
    with st.container(border=True):
        st.markdown("#### 💡 Current Insights")
        
        # Check if we should generate insights
        current_context = st.session_state.get('filter_context')
        cache = st.session_state.insights_cache
        force_regen = st.session_state.get('force_regenerate', False)
        
        # Generate insights when we have context
        if force_regen and current_context:
            with st.spinner("🤖 Generating insights..."):
                try:
                    prompt = generate_auto_insight_prompt(current_context)
                    insights = get_auto_insights_from_agent(prompt, current_context)
                    cache.store(current_context, insights)
                    st.session_state.force_regenerate = False
                    
                    st.markdown(insights)
                    st.session_state.insights_history.insert(0, {
                        'timestamp': time.time(),
                        'context': current_context,
                        'insights': insights
                    })
                    st.session_state.insights_history = st.session_state.insights_history[:10]
                except Exception as e:
                    st.error(f"❌ {str(e)}")
        elif current_context and not force_regen:
            # Try to get cached insights
            cached = cache.get_cached(current_context)
            if cached:
                st.markdown(cached['insights'])
                st.caption(f"🕐 Generated at {time.strftime('%H:%M:%S', time.localtime(cached['timestamp']))}")
            else:
                # Generate new
                with st.spinner("🤖 Analyzing..."):
                    try:
                        prompt = generate_auto_insight_prompt(current_context)
                        insights = get_auto_insights_from_agent(prompt, current_context)
                        cache.store(current_context, insights)
                        
                        st.markdown(insights)
                        st.session_state.insights_history.insert(0, {
                            'timestamp': time.time(),
                            'context': current_context,
                            'insights': insights
                        })
                        st.session_state.insights_history = st.session_state.insights_history[:10]
                    except Exception as e:
                        st.error(f"❌ {str(e)}")
        else:
            st.info("👆 Click **Auto-Detect Filters** to read filters from Power BI and generate insights")
    
    st.divider()
    
    # History
    if st.session_state.insights_history:
        with st.expander("📜 Recent Insights", expanded=False):
            for i, item in enumerate(st.session_state.insights_history[:5]):
                ts = time.strftime('%H:%M:%S', time.localtime(item['timestamp']))
                st.caption(f"**{ts}**")
                st.markdown(item['insights'])
                if i < len(st.session_state.insights_history[:5]) - 1:
                    st.divider()
    
    st.divider()
    
    # Manual questions
    st.markdown("#### ❓ Ask a Question")
    with st.form("question_form"):
        question = st.text_area("Your question:", placeholder="e.g., What's the delay trend?", height=80)
        submitted = st.form_submit_button("Ask", use_container_width=True)
        
        if submitted and question.strip():
            with st.spinner("Thinking..."):
                try:
                    from langchain_core.messages import HumanMessage, SystemMessage
                    
                    messages = [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=question)
                    ]
                    
                    response = agent_executor.invoke({"messages": messages})
                    
                    if response and "messages" in response:
                        final_message = response["messages"][-1]
                        if hasattr(final_message, 'content'):
                            output = final_message.content
                        else:
                            output = str(final_message)
                        
                        st.markdown("**Answer:**")
                        st.markdown(output)
                except Exception as e:
                    st.error(f"Error: {str(e)}")

with right:
    auto_insights_panel()