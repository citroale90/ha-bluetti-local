# BLUETTI Local Wi-Fi per Home Assistant

[English documentation](README.en.md) · [Configurazione di rete](docs/network.md)

Integrazione sostitutiva in sola lettura per BLUETTI Elite 30 V2 (EL30V2), derivata dall’integrazione ufficiale BLUETTI 1.0.5. Riceve i dati dell’UPS sulla rete Wi-Fi locale e funziona senza account cloud o collegamento Bluetooth.

Il dominio `bluetti_local` è indipendente da `bluetti` e `bluetti_bt`. La disinstallazione delle altre integrazioni non rimuove questo componente.

## Stato del lavoro

Versione **0.4.1 in anteprima**. Il protocollo è stato verificato su EL30V2 con firmware IoT 8024.14: acquisizione passiva, confronto con la mappa registri e prova diretta del ricevitore. Il collaudo dell’integrazione completa in Home Assistant e la verifica di stabilità prolungata sono registrati separatamente dai test del solo protocollo.

Il supporto di altri modelli BLUETTI richiede prove e mappe registri dedicate.

Collaudo del 2 novembre 2026: 53 test superati in Home Assistant 2026.10.4 isolato, più letture reali riuscite dopo un riavvio di Home Assistant e dopo la ricarica dell’integrazione. Sono stati controllati checksum, coerenza dei campioni e DNS locale. Il collaudo non include un’interruzione dell’alimentazione dell’UPS né dimostra ancora la stabilità per giorni o settimane.

Dalla 0.4.1 la configurazione, le opzioni, i nomi delle 23 entità e gli stati della modalità di ricarica sono disponibili in italiano e in inglese. Home Assistant usa le proprie impostazioni di lingua. Gli aggiornamenti conservano gli ID e i nomi personalizzati delle entità.

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
| Uscita AC abilitata | Stato esplicito del registro 2011, solo lettura |
| Uscita DC abilitata | Stato esplicito del registro 2012, solo lettura |

I sensori diventano `unavailable` quando il collegamento si chiude oppure quando una lettura completa supera 90 secondi. Un valore invariato ricevuto in una nuova lettura aggiorna correttamente la freschezza; un pacchetto duplicato dello stesso ciclo non la rinnova. L’orologio monotono misura la scadenza dei dati. Se arrivano soltanto keepalive MQTT o rapporti incompleti/duplicati per 90 secondi, viene chiusa la sola sessione dati per permettere la riconnessione normale dell’UPS; nessuna azione sulle uscite o sull’alimentazione.

Dalla 0.3.1 i due stati delle uscite sono indicatori `binary_sensor`, non comandi. Il valore 1 indica abilitata, 0 disabilitata; altri valori non vengono interpretati. Un’uscita può essere abilitata anche a 0 W. Il blocco di stato ha checksum, ciclo e freschezza propri (90 secondi): dati assenti, scaduti o una nuova connessione senza un nuovo rapporto rendono gli indicatori indisponibili. Questi rapporti non rinnovano il timestamp delle misure numeriche usate dal watchdog.

### Scoperta continua delle entità

La 0.4.0 riconosce automaticamente **tutti i 19 campi leggibili del profilo EL30V2 noto**. Le 12 entità precedenti conservano i loro identificatori; i campi aggiuntivi diventano entità dopo due rapporti validi di cicli distinti, anche se arrivano dopo l'avvio. Con tutti i campi osservati si ottengono **23 entità**: 19 letture, due potenze totali calcolate e due diagnostiche del collegamento.

| Nuove entità scoperte | Significato |
| --- | --- |
| Tempo rimanente stimato | Valore trasmesso dal dispositivo, in ore; non distinto fra carica e scarica |
| ECO AC / DC attivo | Indicatori in sola lettura |
| ECO AC / DC tempo di spegnimento | Durata configurata: 1, 2, 3 o 4 ore |
| ECO AC / DC soglia di potenza | Soglie configurate, in watt |
| Modalità di ricarica | Standard, Silenziosa o Turbo |
| Power Lifting attivo | Indicatore in sola lettura |
| Modello e numero di serie rilevati | Diagnostica ricavata dal rapporto e verificata con il dispositivo configurato |

Non occorre aggiungere manualmente le entità o ricaricare l'integrazione. Il registro nativo di Home Assistant ne conserva identità, nomi personalizzati e disabilitazioni. Dopo un riavvio vengono ricreate senza ripristinare valori vecchi; tornano disponibili quando arrivano dati validi. Ogni nuovo campo ha una propria scadenza di 90 secondi: altri rapporti non ne rinnovano la freschezza e non aggiornano il timestamp numerico usato dal watchdog.

**Il dispositivo non invia un catalogo con i significati dei registri.** La scoperta riguarda il profilo verificato in `profile.py`; registri sconosciuti e valori non supportati non producono entità inventate. Il supporto di nuovi campi/modelli richiede una mappa verificata. La diagnostica mostra i campi noti osservati/confermati/recenti, senza valori, seriali o pacchetti grezzi.

La mappa è confrontata con [bluetti-bt-lib per EL30V2](https://github.com/Patrick762/bluetti-bt-lib/blob/main/bluetti_bt_lib/devices/el30v2.py) e con i rapporti Wi-Fi: **19 campi, 87 confronti concordanti, almeno quattro cicli distinti per campo**. Il decodificatore è indipendente e non richiede quella libreria né avvia il Bluetooth. La 0.4.0 supera **114 test isolati** in Home Assistant 2026.10.4, compresi creazione a caldo, ricarica senza doppioni, disabilitazioni utente e scadenza dei singoli campi. Il collaudo fisico legge gli stati già presenti: non sono state commutate le uscite.

Questa versione espone il monitoraggio. Non registra servizi, pulsanti, interruttori AC/DC, comandi di spegnimento o aggiornamenti del firmware.

## Installazione locale

### Tramite HACS

1. In HACS apri il menu **Repository personalizzati**.
2. Inserisci `https://github.com/citroale90/ha-bluetti-local` e scegli la categoria **Integrazione**.
3. Scarica **BLUETTI Local Wi-Fi**, scegliendo la versione in anteprima se HACS lo richiede, e riavvia Home Assistant.
4. Aggiungi l’integrazione e scegli la modalità di rete **DNS diretto**, **Automatica** oppure **Manuale**, come descritto sotto.

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

La versione 0.3.0 gestisce direttamente le regole quando Home Assistant gira **sul router Linux dell’UPS**, nella stessa rete dell’host, e dispone già del permesso `NET_ADMIN`. La procedura guidata verifica i requisiti, chiede l’interfaccia LAN e mostra l’ambito prima della conferma. HACS non può concedere da solo questi permessi o configurare un router separato.

**Non viene installato alcun servizio aggiuntivo:** niente helper sul router, script esterno, timer systemd, account SSH, broker MQTT separato o nuove dipendenze. Il codice del componente usa direttamente l’interfaccia nftables del kernel e una propria tabella dedicata, limitata all’IP e all’interfaccia dell’UPS.

Il ricevitore parte prima delle regole. Servono due nuove letture valide entro tre minuti, altrimenti le nuove regole vengono rimosse. Se una vecchia connessione cloud impedisce il collegamento locale, viene fatta scadere soltanto la connessione TCP/18760 dell’UPS: nessuno svuotamento generale delle connessioni, nessun comando AC/DC. Un controllo nativo di Home Assistant verifica le regole ogni 30 secondi e le ripristina se un ricaricamento del firewall le ha eliminate.

Arrestare, ricaricare, disabilitare o rimuovere l'integrazione elimina le sue regole, incluse quelle già verificate. Anche l'annullamento del task di verifica ripulisce le regole: non viene installato un blocco nel firewall di avvio. In modalità Automatica il DNS mantiene l'indirizzo originale del produttore, così, senza i reindirizzamenti, il Bluetti può ritentare il percorso cloud. La terminazione forzata del processo senza eseguire le procedure di arresto può lasciare le regole nel kernel fino alla successiva pulizia o al riavvio dell'host. Le vecchie regole manuali esterne restano indipendenti.

Su installazioni diverse sono disponibili DNS diretto e Manuale. La gestione automatica non è dichiarata compatibile con qualunque router, Home Assistant OS o rete Docker.

Collaudo 0.3.0: **82 test isolati** e una prova reale del kernel in reti Linux separate, con DNS UDP/TCP, traffico MQTT, esclusione degli altri client, applicazione ripetuta, scadenza mirata delle connessioni e conservazione delle tabelle estranee. Collaudo reale del 3 novembre: modalità Automatica confermata da letture nuove, vecchie regole manuali rimosse e acquisizione riuscita anche dopo la ricarica della sola integrazione. Non sono state azionate le uscite AC/DC o interrotta l’alimentazione. La stabilità per giorni o settimane resta da verificare.

## DNS diretto con un router domestico

Questa modalità collega il Bluetti direttamente al ricevitore MQTT di Home Assistant, anche quando Home Assistant gira su un Raspberry o NAS separato dal router. Il firmware deve usare il DNS locale configurato: il componente non può modificare a distanza le impostazioni DHCP di un router arbitrario.

1. Riserva un IP all'UPS e all'host Home Assistant; il ricevitore deve essere raggiungibile dalla LAN sulla porta TCP **18760**.
2. Scegli **DNS diretto** nella procedura guidata.
3. Se hai già un resolver locale, scegli **DNS esterno** e crea quattro riscritture A verso l'IP di Home Assistant: `iot.bluettipower.com`, `gw.bluettipower.com`, `gwpry.bluettipower.com`, `iotde.bluettipower.com`. Il componente non apre il proprio socket DNS in questa variante. Verifica che non restino risposte AAAA dirette al cloud per questi nomi. Le riscritture globali possono interessare altri dispositivi Bluetti: usa l'ambito del singolo client, se il resolver lo consente.
4. In alternativa scegli **DNS integrato**: ascolta sull'IP di Home Assistant, UDP/TCP **53**, e risponde soltanto all'IP dell'UPS con l'indirizzo del ricevitore. La porta deve essere disponibile e accessibile dalla LAN, con i permessi di bind necessari. Assegna questo DNS tramite una voce DHCP dedicata al solo UPS. **Non impostarlo come DNS generale della casa**: non risolve altri domini o client e non accetta un proxy DNS del router come sorgente.
5. Conferma DNS/DHCP e verifica campioni completi nuovi. Nessuna regola NAT viene creata. Il ricevitore MQTT resta dentro l'integrazione: non occorre un broker separato.

Le modifiche DNS del router/resolver restano a carico dell'amministratore e non vengono cancellate dal plugin. Il ritorno al cloud a integrazione spenta dipende da quelle impostazioni e dalla cache del dispositivo: questa modalità non garantisce un fallback automatico. Sulle installazioni Home Assistant OS o Container occorre verificare anche l'accessibilità della porta DNS; HACS da solo non pubblica porte o concede permessi.

Il collegamento diretto con host separato, DNS UDP/TCP, MQTT e riconnessione è verificato in reti Linux isolate, senza NAT né Internet reale. Il dispositivo fisico è stato verificato con la modalità Automatica; la prova DNS diretto sul Bluetti reale resta distinta e non è ancora dichiarata superata.

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
