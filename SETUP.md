# Generatore Note Proforma – Il Mannarino SRL
## Guida al deploy su Railway

---

### Prerequisiti
- Account Railway (railway.app)
- Istanza n8n attiva con i due workflow importati e configurati
- Google Drive (o OneDrive) collegato a n8n

---

### 1. Importa i workflow n8n

Importa i due file JSON forniti separatamente nel tuo n8n:
- **Proforma – Ricerca Clienti**: gestisce l'autocompletamento clienti
- **Proforma – Storage PDF**: salva i PDF generati su OneDrive

Dopo l'importazione:
1. Collega le credenziali (database clienti, OneDrive)
2. Attiva entrambi i workflow
3. Copia gli URL dei webhook (serviranno al passo 3)

---

### 2. Crea il progetto su Railway

1. Vai su [railway.app](https://railway.app) e fai login
2. **New Project → Deploy from GitHub repo**
3. Collega il repository GitHub con i file di questa cartella
4. Railway rileva automaticamente Python e usa il `Procfile`

---

### 3. Configura le variabili d'ambiente

Nel pannello Railway del tuo progetto, vai su **Variables** e aggiungi:

| Variabile | Valore |
|---|---|
| `N8N_SEARCH_URL` | URL webhook n8n ricerca clienti |
| `N8N_STORAGE_URL` | URL webhook n8n salvataggio PDF |

Esempio:
```
N8N_SEARCH_URL=https://tuo-n8n.dominio.com/webhook/proforma/cerca-clienti
N8N_STORAGE_URL=https://tuo-n8n.dominio.com/webhook/proforma/salva-pdf
```

---

### 4. Deploy

Railway fa il deploy automaticamente dopo ogni push su GitHub.
Il primo deploy impiega qualche minuto per installare le dipendenze.

Al termine trovi l'URL pubblico dell'app nel pannello Railway (es. `https://nome-progetto.up.railway.app`).

---

### 5. Aggiornare gli store

Per aggiungere o rimuovere store, modifica il file `stores.json` e fai push su GitHub.
Railway fa il redeploy automatico.

---

### Contatore proforma

Il contatore progressivo (`counter.json`) è persistente solo se Railway ha un **volume** collegato.
Senza volume, si azzera ad ogni redeploy. Per abilitare la persistenza:
1. In Railway, aggiungi un **Volume** al servizio
2. Imposta il mount path su `/data`
3. Aggiungi la variabile d'ambiente: `DATA_DIR=/data`
