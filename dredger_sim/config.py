"""Configurazione condivisa del simulatore."""

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
HTML_PATH = PROJECT_DIR / "mappa.html"
CURSOR_IMAGE_PATH = PROJECT_DIR / "DRP ombra.png"
TARGET_IMAGE_PATH = PROJECT_DIR / "gps.png"

HOST = "127.0.0.1"

TCP_BIND_HOST = "0.0.0.0"

TCP_PORT = 5020

SEND_INTERVAL_SECONDS = 1

COORDINATE_SCALE = 10  # metri -> centimetri

OPCUA_ENDPOINT = "opc.tcp://192.168.10.30:4840"

OPCUA_POLL_SECONDS = 1

GRID_POLL_SECONDS = 5

GRID_ROWS = 25  # array PLC esportato come [0..24][0..24]

GRID_COLUMNS = 25

GRID_READ_BATCH_SIZE = 100  # limite operativo osservato sul server OPC UA del PLC

GRID_CELL_PROPERTIES = (
    "Path_Index",
    "First_Depth_Read_cm",
    "Last_Depth_Read_cm",
    "Target_Depth_cm",
    "Center_Relative_North_dm",
    "Center_Relative_East_dm",
    "Edges_Crossed",
    "Error",
)

OPCUA_TAGS = {
    "UTM_North_Offset": ["IO", "GPS", "Sts", "UTM_North_Offset"],
    "UTM_East_Offset": ["IO", "GPS", "Sts", "UTM_East_Offset"],
    "UTM_North_ref_points": ["IO", "GPS", "Cfg", "stRef_Points", "UTM_North"],
    "UTM_East_ref_points": ["IO", "GPS", "Cfg", "stRef_Points", "UTM_East"],
}
