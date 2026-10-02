# Simulatore di navigazione draga

Versione Windows con mappa Leaflet in Google Chrome e server TCP personalizzato in Python. Il PLC/CR1074 si collega come client TCP; Python invia una riga di testo subito alla connessione e poi ogni secondo. Tkinter e Modbus non sono usati.

Il codice è organizzato per responsabilità nella cartella `dredger_sim/`:

- `config.py`: indirizzi, porte e parametri;
- `state.py`: posizione, heading e stato condiviso;
- `tcp_server.py`: invio periodico dei dati GPS al PLC;
- `opcua_client.py`: connessione OPC UA e lettura del reticolo;
- `coordinates.py`: conversioni UTM/WGS84;
- `web_server.py`: pagina, API HTTP e comandi di movimento.

`simulatore_draga.py` resta il punto d’ingresso da avviare.

## Requisiti

- Windows 10/11
- Python 3.10 o successivo
- Google Chrome
- Internet per caricare Leaflet e le tessere OpenStreetMap

## Installazione

Apri PowerShell in questa cartella ed esegui:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Se PowerShell impedisce l'attivazione dell'ambiente:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Avvio e uso

Con l'ambiente virtuale attivo:

```powershell
python simulatore_draga.py
```

Lo script avvia un server HTTP solo sul PC per la pagina e un server TCP sulla porta **5020**, poi apre Chrome. Lascia aperto il terminale mentre usi il simulatore; premi `Ctrl+C` per arrestarlo.

1. Configura nel client TCP del PLC l'IP del PC sulla rete Ethernet (nel caso attuale `192.168.10.216`) e la porta **5020**.
2. Se Windows Firewall chiede conferma, consenti la comunicazione TCP in ingresso sulla rete privata.
3. Clicca sulla mappa per posizionare la draga. Usa le quattro frecce nella banda laterale per spostarla lungo gli assi cardinali UTM e i due pulsanti di rotazione per modificare la direzione. I passi di spostamento e rotazione sono regolabili. Il prossimo messaggio TCP userà i valori aggiornati; l'invio continua ogni secondo.

Il server ascolta su tutte le interfacce del PC. Se la porta è già occupata, cambia `TCP_PORT` in `simulatore_draga.py` e usa lo stesso numero nel PLC.

## Formato del messaggio

È una riga ASCII con campi separati da `;`, nell'ordine richiesto, terminata da `CRLF` (`\r\n`). Il server invia subito una riga quando il PLC si collega e ripete ogni secondo.

```text
North;East;WGS84_Height;Zone;Band;usi_Band;HDT;Solution_Fix;Sat_Number;Diff_Age\r\n
```

Esempio per una posizione nel Lago Inferiore, in zona UTM 32T:

```text
500000000;60000000;0;32;T;16;0;0;0;0
```

| Campo | Tipo PLC | Formato/unità iniziale |
|---|---|---|
| North | LINT | Northing UTM in centimetri (100 unità per metro) |
| East | LINT | Easting UTM in centimetri (100 unità per metro) |
| WGS84_Height | LINT | Millimetri; per ora `0` perché la mappa non fornisce quota |
| Zone | USINT | Numero zona UTM, da 1 a 60 |
| Band | STRING | Lettera della banda UTM, per esempio `T` |
| usi_Band | USINT | Codice numerico convenuto: C=1, D=2, …, X=20; I e O escluse |
| HDT | UINT | Heading simulato in centesimi di grado (es. 12345 = 123,45°; 0°=Nord, crescente in senso orario) |
| Solution_Fix | USINT | Per ora `0`; il significato dei codici fix va concordato con il PLC |
| Sat_Number | USINT | Numero satelliti; per ora `0` |
| Diff_Age | UINT | Età correzione in secondi; per ora `0` |

Prima di selezionare un punto, i campi posizione sono a zero e `Band` è vuoto. Per ora solo North, East, Zone, Band e usi_Band sono derivati dalla mappa; gli altri sono segnaposto, non misure GPS reali.

Il TCP è un flusso di byte: una singola chiamata `Read()` nel PLC può ricevere una riga intera o una sua parte. Accumula i byte nel buffer fino al terminatore `CRLF`, processa la riga completa e conserva gli eventuali byte successivi per la lettura seguente.

## Mappa

La mappa parte dal Lago Inferiore (circa 45.1515, 10.8158), ma puoi navigare in altre zone. Questa versione usa tessere online: non carica ancora `Lago_Inferiore.osm` offline.

La pagina comunica con Python attraverso un server HTTP locale limitato a `127.0.0.1`. Il server TCP personalizzato è invece raggiungibile dalla rete del PLC; non implementa autenticazione o cifratura, quindi usalo solo sulla rete di prova isolata.

L'uso delle tessere OpenStreetMap segue la [Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/); sulla mappa è mostrata l'attribuzione.
