# BLUETTI Local Wi-Fi per Home Assistant

[English documentation](README.en.md) · [Configurazione di rete](docs/network.md)

Integrazione sostitutiva in sola lettura per BLUETTI Elite 30 V2 (EL30V2), derivata dall’integrazione ufficiale BLUETTI 1.0.5. Riceve i dati dell’UPS sulla rete Wi-Fi locale e funziona senza account cloud o collegamento Bluetooth.

Il dominio `bluetti_local` è indipendente da `bluetti` e `bluetti_bt`. La disinstallazione delle altre integrazioni non rimuove questo componente.

## Stato del lavoro

Versione **0.1.1 in anteprima**. Il protocollo è stato verificato su EL30V2 con firmware IoT 8024.14: acquisizione passiva, confronto con la mappa registri e prova diretta del ricevitore. Il collaudo dell’integrazione completa in Home Assistant e la verifica di stabilità prolungata sono registrati separatamente dai test del solo protocollo.

Il supporto di altri modelli BLUETTI richiede prove e mappe registri dedicate.

Collaudo del 2 novembre 2026: 53 test superati in Home Assistant 2026.10.4 isolato, più letture reali riuscite dopo un riavvio di Home Assistant e dopo la ricarica dell’integrazione. Sono stati controllati checksum, coerenza dei campioni e DNS locale. Il collaudo non include un’interruzione dell’alimentazione dell’UPS né dimostra ancora la stabilità per giorni o settimane.

## Icona locale

L’icona Bluetti originale è inclusa in `custom_components/bluetti_local/brand`, in risoluzione 256 e 512 pixel. Home Assistant la serve localmente anche senza Internet; i temi chiari e scuri usano lo stesso simbolo azzurro. Non serve registrare il dominio `bluetti_local` nel CDN dei marchi.

Se aggiungi questa cartella a un’integrazione già caricata, riavvia Home Assistant una volta per aggiornare l’elenco dei suoi file e poi ricarica la pagina o l’app. Origine e attribuzione delle immagini sono in `brand/README.md`.

## Entità disponibili

| Entità | Origine |
| --- | --- |
| Batteria | Registro SOC |
| Uscita AC e uscita DC | Registri di potenza |
| Ingresso rete e ingresso DC | Registri di potenza |
| Tensione rete | Registro di tensione |
| Uscita totale e ingresso totale | Somma delle rispettive potenze della stessa lettura |
| Ultima lettura valida | Data e ora di ricezione di una lettura completa |
| Telemetria locale | Collegamento attivo con una lettura valida e recente |

I sensori diventano `unavailable` quando il collegamento si chiude oppure quando una lettura completa supera 90 secondi. Un valore invariato ricevuto in una nuova lettura aggiorna correttamente la freschezza; un pacchetto duplicato dello stesso ciclo non la rinnova. L’orologio monotono misura la scadenza dei dati.

Questa versione espone il monitoraggio. Non registra servizi, pulsanti, interruttori AC/DC, comandi di spegnimento o aggiornamenti del firmware.

## Installazione locale

### Tramite HACS

1. In HACS apri il menu **Repository personalizzati**.
2. Inserisci `https://github.com/citroale90/ha-bluetti-local` e scegli la categoria **Integrazione**.
3. Scarica **BLUETTI Local Wi-Fi**, scegliendo la versione in anteprima se HACS lo richiede, e riavvia Home Assistant.
4. Aggiungi l’integrazione e configura il percorso dati/DNS come nei passaggi seguenti.

Il repository è utilizzabile come repository personalizzato: non è dichiarato incluso nel catalogo predefinito HACS. La versione minima indicata nei metadati è Home Assistant **2026.10.4**, la versione effettivamente collaudata. Versioni precedenti non sono state verificate.

### Copia manuale e prima configurazione

1. Copia la cartella `custom_components/bluetti_local` nella cartella `custom_components` della configurazione di Home Assistant.
2. Riavvia Home Assistant per caricare il nuovo componente.
3. Apri **Impostazioni → Dispositivi e servizi → Aggiungi integrazione → BLUETTI Local Wi-Fi**.
4. Indica il nome desiderato, l’IP riservato all’UPS, il suo numero di serie senza il prefisso `EL30V2`, l’IP locale raggiungibile di Home Assistant e le porte dei ricevitori: normalmente `18760` per i dati e `25354` per il DNS locale dell’UPS.
5. Configura sul router il percorso locale del solo canale dati dell’UPS, come descritto sotto.
6. Verifica almeno due letture complete, la data dell’ultima lettura e il comportamento alla disconnessione. Completa queste verifiche prima di rimuovere le precedenti integrazioni.

Home Assistant deve poter ascoltare sull’indirizzo configurato. Con Home Assistant Container in rete host, usa l’IP LAN dell’host. Con reti Docker bridge occorre pubblicare la porta e verificare che l’indirizzo sorgente dell’UPS resti riconoscibile.

## Percorso di rete necessario

L’EL30V2 apre una connessione MQTT in uscita sulla porta TCP `18760`. Per renderla locale il router deve indirizzare **solo quel traffico dell’UPS** verso l’IP e la porta del ricevitore Home Assistant.

Le regole possono essere DNAT limitati a IP dell’UPS, interfaccia LAN e porta di destinazione: TCP `18760` verso il ricevitore dati, UDP e TCP `53` verso il ricevitore DNS locale `25354`. Le impostazioni degli altri dispositivi e il DNS generale restano indipendenti. Il componente Home Assistant non modifica firewall, DHCP o DNS.

La topologia collaudata ha Home Assistant Container in rete host sul router Linux. La [guida di rete](docs/network.md) contiene un esempio limitato a questa topologia, verifiche e rimozione delle sole regole dedicate. Un’installazione Home Assistant separata dal router richiede una valutazione del percorso di ritorno: il solo DNAT può non bastare.

Con il percorso locale attivo, l’app Bluetti può indicare l’unità offline nel cloud. Il ricevitore risponde a CONNECT, SUBSCRIBE e PING e conferma le eventuali pubblicazioni QoS 1; non inoltra i comandi cloud.

Per il ripristino rimuovi le tre regole dedicate, dati e DNS, e riconnetti o scarica questa integrazione: l’UPS torna a tentare il collegamento alla destinazione originale. La gestione persistente delle regole appartiene al firewall del router.

Il DNS locale risponde per `iot.bluettipower.com`, `gw.bluettipower.com`, `gwpry.bluettipower.com` e `iotde.bluettipower.com`; rifiuta altre richieste e non fa ricerche su Internet. Gateway iniziale e broker europeo servono entrambi per una riconnessione completa dell’EL30V2: supportare solo il primo nome storico permette di funzionare con la cache già popolata, ma impedisce il recupero successivo. Restituisce l’indirizzo di instradamento del broker osservato, `47.254.169.86`, mentre il DNAT del solo UPS mantiene il collegamento dentro la LAN. Questo evita di lasciare nella cache dell’UPS l’indirizzo privato del ricevitore quando ripristini il percorso originale. Le risposte AAAA non contengono indirizzi IPv6 cloud.

Il timestamp del messaggio iniziale segue il formato osservato sul servizio di bootstrap, UTC meno otto ore. Questo adattamento riguarda soltanto il protocollo: le date dei sensori e della freschezza restano in UTC, visualizzate nel fuso orario di Home Assistant.

## Automazioni e migrazione

Il componente non installa automazioni di arresto delle macchine. Le automazioni esistenti devono essere ricontrollate quando cambia l’entità sorgente. Usa disponibilità, `last_successful_poll`, `validated_crc` e `transport: wifi_local`; non trattare `unavailable` come batteria a zero o rete elettrica assente.

I sensori hanno identificatori propri stabili. Il passaggio da entità Bluetooth o cloud con altri identificatori richiede una migrazione controllata dei riferimenti nelle plance e nelle automazioni. Disabilitare una vecchia integrazione conserva la sua configurazione; rimuoverla elimina le sue entità dal registro di Home Assistant.

## Sicurezza e diagnostica

Il protocollo dell’UPS osservato è MQTT non cifrato. Il ricevitore è destinato a una LAN fidata, con indirizzo sorgente limitato all’UPS e senza esposizione WAN. Il CRC controlla l’integrità dei dati; non è una firma crittografica.

Le credenziali eventualmente presenti nel CONNECT dell’UPS vengono elaborate soltanto in memoria. Non sono salvate né scritte nei log. La diagnostica omette seriali, indirizzi, nomi e contenuti dei pacchetti.

## Licenza e origine

Licenza MIT, con attribuzione a Bluetti. Consulta [LICENSE](LICENSE), [NOTICE](NOTICE) e [provenance.json](provenance.json).

La correzione della libreria Bluetooth è un lavoro separato e non fa parte di questa integrazione Wi-Fi.

## Test ripetibili

Su un host Linux con l’immagine Home Assistant già presente:

```bash
bash tools/test-in-homeassistant.sh
```

Se il container non si chiama `home-assistant`, specifica l’immagine locale:

```bash
HA_IMAGE=homeassistant/home-assistant:2026.10.4 bash tools/test-in-homeassistant.sh
```

Lo script non scarica immagini. Avvia un processo separato senza rete, senza dispositivi e senza montare la configurazione in uso; la sola sorgente di questa integrazione viene montata in lettura. Copre protocollo, gateway iniziale, DNS UDP/TCP, ciclo reale di configurazione e sensori Home Assistant, disconnessione, riconnessione, scadenza e rifiuto di sorgenti diverse dall’UPS.
