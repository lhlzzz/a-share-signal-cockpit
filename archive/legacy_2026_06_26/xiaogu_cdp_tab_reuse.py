"""CDP tab reuse module - reuse tabs instead of opening new ones."""
import json
import time
import urllib.request
from urllib.parse import quote


def get_cdp_tabs(cdp_url):
    """Get all tabs from CDP."""
    resp = urllib.request.urlopen(f"{cdp_url.rstrip('/')}/json")
    return json.loads(resp.read())


def close_all_tabs(cdp_url):
    """Close all tabs except the first one."""
    tabs = get_cdp_tabs(cdp_url)
    closed = 0
    for tab in tabs[1:]:
        try:
            close_url = f"{cdp_url.rstrip('/')}/json/close/{tab['id']}"
            urllib.request.urlopen(close_url)
            closed += 1
        except Exception:
            pass
    return closed


def navigate_tab(cdp_url, tab_id, url, timeout=30):
    """Navigate an existing tab to a new URL."""
    # Use CDP protocol to navigate
    ws_url = None
    tabs = get_cdp_tabs(cdp_url)
    for tab in tabs:
        if tab.get('id') == tab_id:
            ws_url = tab.get('webSocketDebuggerUrl')
            break
    
    if not ws_url:
        # Fallback: use HTTP endpoint
        navigate_url = f"{cdp_url.rstrip('/')}/json/navigate/{tab_id}?url={quote(url, safe='')}"
        try:
            urllib.request.urlopen(navigate_url)
            time.sleep(2)
            return True
        except Exception:
            return False
    
    # Use WebSocket for more reliable navigation
    import websocket
    ws = websocket.create_connection(ws_url)
    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Page.navigate",
            "params": {"url": url}
        }))
        result = json.loads(ws.recv())
        time.sleep(2)
        return True
    finally:
        ws.close()


def extract_table_data(cdp_url, tab_id):
    """Extract table data from current page."""
    ws_url = None
    tabs = get_cdp_tabs(cdp_url)
    for tab in tabs:
        if tab.get('id') == tab_id:
            ws_url = tab.get('webSocketDebuggerUrl')
            break
    
    if not ws_url:
        return None
    
    import websocket
    ws = websocket.create_connection(ws_url)
    try:
        # Evaluate JavaScript to extract table data
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": """
                    (() => {
                        const tables = document.querySelectorAll('table');
                        const result = [];
                        tables.forEach(table => {
                            const rows = table.querySelectorAll('tr');
                            rows.forEach(row => {
                                const cells = row.querySelectorAll('td, th');
                                const rowData = Array.from(cells).map(cell => cell.textContent.trim());
                                if (rowData.length > 0) result.push(rowData);
                            });
                        });
                        return JSON.stringify(result);
                    })()
                """,
                "returnByValue": True
            }
        }))
        result = json.loads(ws.recv())
        if 'result' in result and 'result' in result['result']:
            return json.loads(result['result']['result'].get('value', '[]'))
        return None
    finally:
        ws.close()


class ReusableTabManager:
    """Manager for reusing CDP tabs."""
    
    def __init__(self, cdp_url):
        self.cdp_url = cdp_url
        self.tabs = {}
        self._ensure_single_tab()
    
    def _ensure_single_tab(self):
        """Ensure there's exactly one tab open."""
        close_all_tabs(self.cdp_url)
        tabs = get_cdp_tabs(self.cdp_url)
        if not tabs:
            # Open a blank tab
            open_url = f"{self.cdp_url.rstrip('/')}/json/new?about:blank"
            urllib.request.urlopen(open_url)
            tabs = get_cdp_tabs(self.cdp_url)
        if tabs:
            self.tabs['default'] = tabs[0]['id']
    
    def get_tab(self, name='default'):
        """Get tab ID by name."""
        return self.tabs.get(name, self.tabs.get('default'))
    
    def navigate(self, url, name='default', timeout=30):
        """Navigate tab to URL."""
        tab_id = self.get_tab(name)
        if not tab_id:
            return False
        return navigate_tab(self.cdp_url, tab_id, url, timeout)
    
    def extract(self, name='default'):
        """Extract table data from current page."""
        tab_id = self.get_tab(name)
        if not tab_id:
            return None
        return extract_table_data(self.cdp_url, tab_id)
    
    def navigate_and_extract(self, url, name='default', wait=3):
        """Navigate to URL, wait, and extract data."""
        self.navigate(url, name)
        time.sleep(wait)
        return self.extract(name)
