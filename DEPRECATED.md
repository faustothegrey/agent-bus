# ⛔ DEPRECATED — Agent Bus

**agent-bus (server.py)** è deprecato.

Il broker HTTP su porta 9901 non serve più. agentctl ora comunica direttamente con gli agenti via tmux, senza intermediari.

## Cosa è ancora attivo

| Cosa | Stato | Note |
|---|---|---|
| **agentctl** | ✅ Attivo | CLI per gestire agenti in tmux |
| **wrapper.sh** | ✅ Attivo | Lancia agenti, salva log, monitora inbox |
| **server.py** | ⛔ Deprecato | Rimosso — agentctl parla diretto via tmux |

## Perché

- server.py era un single-thread HTTP bottleneck
- Esponeva API pericolose (inject, capture) che Hermes usava male
- In memoria — perdeva tutto al crash
- Il vero lavoro lo fa tmux + wrapper.sh, server.py era solo un intermediario inutile

## Riferimenti

- agentctl: `~/Software/scripts-ai/agent-bus/agentctl`
- wrapper.sh: `~/Software/scripts-ai/agent-bus/wrapper.sh`
- Plist disabilitato: `~/Software/scripts-ai/agent-bus/com.fausto.agent-bus.plist`
