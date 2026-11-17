# civento-dati

Dati pubblici usati dall'app CIVENTO.

`allerte.json` contiene l'ultimo **bollettino nazionale di criticità idrogeologica e idraulica**
del Dipartimento della Protezione Civile, in formato compatto: per ogni giorno (oggi e, quando
pubblicato, domani) il livello di allerta di ciascuna delle zone di allerta per rischio
idrogeologico, idraulico e temporali, più l'elenco dei comuni di ogni zona.

Il file viene aggiornato automaticamente ogni 15 minuti da GitHub Actions
(`.github/workflows/aggiorna-allerte.yml`) e cambia solo quando la Protezione Civile pubblica
un nuovo bollettino.

## Fonte e licenza

- Fonte: Dipartimento della Protezione Civile –
  [DPC-Bollettini-Criticita-Idrogeologica-Idraulica](https://github.com/pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica)
- Licenza dei dati: [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.it)

I dati sono riportati senza modifiche nei livelli; la conversione riguarda solo il formato.
Per le decisioni fanno fede i bollettini ufficiali della Protezione Civile e delle Regioni.
