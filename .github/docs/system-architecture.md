# System Architecture

## Data Flow

```mermaid
flowchart LR
    subgraph Venus["Venus OS"]
        MQTT["MQTT Broker"]
        INV["inverter-control"]
    end

    subgraph Dashboard["inverter-dashboard"]
        WS["WebSocket"]
        API["API Server"]
        HA["Home Assistant\n(optional)"]
    end

    subgraph Client["Browser Clients"]
        UI["Single Page App"]
    end

    INV -->|"inverter/state"| MQTT
    MQTT -->|"subscribe"| WS
    WS -->|"push state"| UI
    HA -->|"sensor data"| API
    API -->|"merge"| WS

    style WS fill:#4ecdc4,color:#fff
    style API fill:#9b59b6,color:#fff
```

## WebSocket Update Flow

```mermaid
sequenceDiagram
    participant Broker as MQTT Broker
    participant WS as WebSocket Server
    participant UI as Browser UI

    Broker->>WS: inverter/state JSON
    WS->>WS: Parse & validate
    WS->>UI: WebSocket frame
    UI->>UI: Update charts/cards

    Note over UI: uPlot re-renders
```

## State Merging

```mermaid
flowchart TB
    subgraph Sources["State Sources"]
        MQTT["inverter/state\nfrom Cerbo"]
        HA["HA sensors\nDirect poll"]
    end

    subgraph Merge["State Merge"]
        S1["Base MQTT state"]
        S2["HA sensor data"]
        M["Merge logic"]
    end

    subgraph Output["Final State"]
        F["Combined state\nfor UI"]
    end

    MQTT --> S1
    HA --> S2
    S1 --> M
    S2 --> M
    M --> F

    style M fill:#9b59b6,color:#fff
    style F fill:#4ecdc4,color:#fff
```

## Runbook: Troubleshooting

### Dashboard Shows "Connecting..."

**Symptoms:**
- WebSocket never establishes
- Dashboard stuck on loading screen

**Actions:**
```bash
# Check MQTT connectivity
docker exec inverter-dashboard nc -zv MQTT_HOST 1883

# Verify broker has data
docker exec inverter-dashboard mosquitto_sub -v -t 'inverter/state' -C 1

# Check logs
docker logs inverter-dashboard --tail 50
```

### Stale Data

**Symptoms:**
- Dashboard shows old values
- Charts not updating

**Actions:**
```bash
# Verify inverter-control is publishing
mosquitto_sub -v -t 'inverter/state' -C 5

# Check WebSocket connection
# Open browser devtools → Network → ws://... → messages

# Restart dashboard
docker restart inverter-dashboard
```

### Home Assistant Sensors Missing

**Symptoms:**
- Appliance status not showing
- Water level N/A

**Actions:**
```bash
# Verify HA is reachable
curl -s -H "Authorization: Bearer $HA_TOKEN" \
  $HA_URL/api/states/sensor.home_totalusage_1s | jq

# Check local_config.py has correct entity IDs
docker exec inverter-dashboard cat /app/config/local_config.py
```

---

## Related Documentation

- [inverter-control System Architecture](../inverter-control/.github/docs/system-architecture.md)
- [ADR-001: MQTT Bridge Architecture](../inverter-control/.github/docs/adr-001-grid-zero-architecture.md)
