# BLUETTI Local Wi-Fi per Home Assistant

[English documentation](README.en.md) · [Configurazione di rete](docs/network.md)

Integrazione sostitutiva in sola lettura per BLUETTI Elite 30 V2 (EL30V2), derivata dall’integrazione ufficiale BLUETTI 1.0.5. Riceve i dati dell’UPS sulla rete Wi-Fi locale e funziona senza account cloud o collegamento Bluetooth.

Il dominio `bluetti_local` è indipendente da `bluetti` e `bluetti_bt`. La disinstallazione delle altre integrazioni non rimuove questo componente.

## Stato del lavoro

Versione **0.2.0 in anteprima**. Il protocollo è stato verificato su EL30V2 con firmware IoT 8024.14: acquisizione passiva, confronto con la mappa registri e prova diretta del ricevitore. Il collaudo dell’integrazione completa in Home Assistant e la verifica di stabilità prolungata sono registrati separatamente dai test del solo protocollo.

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

I sensori diventano `unavailable` quando il collegamento si chiude oppure quando una lettura completa supera 90 secondi. Un valore invariato ricevuto in una nuova lettura aggiorna correttamente la freschezza; un pacchetto duplicato dello stesso ciclo non la rinnova. L’orologio monotono misura la scadenza dei dati. Se arrivano soltanto keepalive MQTT o rapporti incompleti/duplicati per 90 secondi, viene chiusa la sola sessione dati per permettere la riconnessione normale dell’UPS; nessuna azione sulle uscite o sull’alimentazione.

Questa versione espone il monitoraggio. Non registra servizi, pulsanti, interruttori AC/DC, comandi di spegnimento o aggiornamenti del firmware.

## Installazione locale

### Tramite HACS

1. In HACS apri il menu **Repository personalizzati**.
2. Inserisci `https://github.com/citroale90/ha-bluetti-local` e scegli la categoria **Integrazione**.
3. Scarica **BLUETTI Local Wi-Fi**, scegliendo la versione in anteprima se HACS lo richiede, e riavvia Home Assistant.
4. Aggiungi l’integrazione e scegli la modalità di rete **Automatica** oppure **Manuale**, come descritto sotto.

Il repository è utilizzabile come repository personalizzato: non è dichiarato incluso nel catalogo predefinito HACS. La versione minima indicata nei metadati è Home Assistant **2026.10.4**, la versione effettivamente collaudata. Versioni precedenti non sono state verificate.

### Copia manuale e prima configurazione

1. Copia la cartella `custom_components/bluetti_local` nella cartella `custom_components` della configurazione di Home Assistant.
2. Riavvia Home Assistant per caricare il nuovo componente.
3. Apri **Impostazioni → Dispositivi e servizi → Aggiungi integrazione → BLUETTI Local Wi-Fi**.
4. Indica il nome desiderato, l’IP riservato all’UPS, il suo numero di serie senza il prefisso `EL30V2`, l’IP locale raggiungibile di Home Assistant e le porte dei ricevitori: normalmente `18760` per i dati e `25354` per il DNS locale dell’UPS.
5. In modalità **Automatica** scegli l’interfaccia LAN e conferma le tre regole del solo UPS; in modalità **Manuale** configura il percorso sul router come descritto nella guida.
6. Verifica almeno due letture complete, la data dell’ultima lettura e il comportamento alla disconnessione. Completa queste verifiche prima di rimuovere le precedenti integrazioni.

Home Assistant deve poter ascoltare sull’indirizzo configurato. Con Home Assistant Container in rete host, usa l’IP LAN dell’host. Con reti Docker bridge occorre pubblicare la porta e verificare che l’indirizzo sorgente dell’UPS resti riconoscibile.

## Configurazione automatica dentro il plugin

La versione 0.2.0 gestisce direttamente le regole quando Home Assistant gira **sul router Linux dell’UPS**, nella stessa rete dell’host, e dispone già del permesso `NET_ADMIN`. La procedura guidata verifica i requisiti, chiede l’interfaccia LAN e mostra l’ambito prima della conferma. HACS non può concedere da solo questi permessi o configurare un router separato.

**Non viene installato alcun servizio aggiuntivo:** niente helper sul router, script esterno, timer systemd, account SSH, broker MQTT separato o nuove dipendenze. Il codice del componente usa direttamente l’interfaccia nftables del kernel e una propria tabella dedicata, limitata all’IP e all’interfaccia dell’UPS.

Il ricevitore parte prima delle regole. Servono due nuove letture valide entro tre minuti, altrimenti le nuove regole vengono rimosse. Se una vecchia connessione cloud impedisce il collegamento locale, viene fatta scadere soltanto la connessione TCP/18760 dell’UPS: nessuno svuotamento generale delle connessioni, nessun comando AC/DC. Un controllo nativo di Home Assistant verifica le regole ogni 30 secondi e le ripristina se un ricaricamento del firewall le ha eliminate.

Passare a Manuale, disabilitare o rimuovere la voce automatica elimina soltanto la sua tabella, se non è stata modificata da altri. Durante lo spegnimento completo di Home Assistant le regole confermate restano al loro posto e vengono riconosciute al ritorno; quelle appena create e non verificate vengono rimosse anche se la configurazione viene annullata. Rimuovi prima la voce dell’integrazione, poi i suoi file. Le vecchie regole manuali restano indipendenti e non vengono cancellate automaticamente.

Su installazioni diverse la modalità Manuale resta disponibile. La gestione automatica non è dichiarata compatibile con qualunque router, Home Assistant OS o rete Docker.

Collaudo 0.2.0: **73 test isolati** e una prova reale del kernel in reti Linux separate, con DNS UDP/TCP, traffico MQTT, esclusione degli altri client, applicazione ripetuta, scadenza mirata delle connessioni e conservazione delle tabelle estranee. Collaudo reale del 3 novembre: modalità Automatica confermata da letture nuove, vecchie regole manuali rimosse e acquisizione riuscita anche dopo la ricarica della sola integrazione. Non sono state azionate le uscite AC/DC o interrotta l’alimentazione. La stabilità per giorni o settimane resta da verificare.

## Percorso di rete necessario

L’EL30V2 apre una connessione MQTT in uscita sulla porta TCP `18760`. Per renderla locale il router deve indirizzare **solo quel traffico dell’UPS** verso l’IP e la porta del ricevitore Home Assistant.

Le regole possono essere DNAT limitati a IP dell’UPS, interfaccia LAN e porta di destinazione: TCP `18760` verso il ricevitore dati, UDP e TCP `53` verso il ricevitore DNS locale `25354`. Le impostazioni degli altri dispositivi e il DNS generale restano indipendenti. In modalità Manuale il componente non modifica il firewall. In modalità Automatica gestisce soltanto le sue tre regole dedicate; non modifica DHCP o configurazione DNS degli altri dispositivi.

La topologia collaudata ha Home Assistant Container in rete host sul router Linux. La [guida di rete](docs/network.md) contiene un esempio limitato a questa topologia, verifiche e rimozione delle sole regole dedicate. Un’installazione Home Assistant separata dal router richiede una valutazione del percorso di ritorno: il solo DNAT può non bastare.

Con il percorso locale attivo, l’app Bluetti può indicare l’unità offline nel cloud. Il ricevitore risponde a CONNECT, SUBSCRIBE e PING e conferma le eventuali pubblicazioni QoS 1; non inoltra i comandi cloud.

Per il ripristino rimuovi le tre regole dedicate, dati e DNS, e riconnetti o scarica questa integrazione: l’UPS torna a tentare il collegamento alla destinazione originale. In modalità Automatica il componente ricrea le proprie regole all’avvio; in modalità Manuale la persistenza resta affidata alla configurazione del router.

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
