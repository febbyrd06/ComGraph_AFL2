(() => {
  const statusText = document.getElementById("data-status");
  const statusDot = document.getElementById("status-dot");
  const errorBanner = document.getElementById("map-error");
  const routeStatus = document.getElementById("route-status");
  const routeResults = document.getElementById("route-results");
  const proposalList = document.getElementById("proposal-list");
  const proposalCount = document.getElementById("proposal-count");
  const hint = document.getElementById("interaction-hint");

  const map = L.map("map", { zoomControl: true }).setView([-7.28, 112.65], 12);
  const baseLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  const boundaryLayer = L.geoJSON(null, {
    style: { color: "#344b55", weight: 2, fillColor: "#e9eeea", fillOpacity: 0.1 },
  }).addTo(map);
  const roadsLayer = L.geoJSON(null, {
    style: { color: "#647780", weight: 1, opacity: 0.5 },
  }).addTo(map);
  const samplesLayer = L.geoJSON(null, {
    pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
      radius: 4,
      color: "#e4f7f0",
      weight: 1,
      fillColor: "#39a882",
      fillOpacity: 0.95,
    }),
    onEachFeature: (feature, layer) => layer.bindPopup(
      `<strong>${escapeHtml(feature.properties.label || feature.properties.sample_id)}</strong><br>` +
      "Synthetic road-aligned demonstration point",
    ),
  }).addTo(map);
  const realStationsLayer = L.geoJSON(null, {
    pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
      radius: 7,
      color: "#fff1f1",
      weight: 1.5,
      fillColor: "#d75555",
      fillOpacity: 1,
    }),
    onEachFeature: (feature, layer) => {
      const props = feature.properties;
      layer.bindPopup(
        `<strong>${escapeHtml(props.name || "Unnamed OSM entry")}</strong><br>` +
        `${escapeHtml(props.category || "REAL-OSM")}<br>` +
        `${escapeHtml(props.verification_status || "OSM location not authority-verified")}<br>` +
        `Source: ${escapeHtml(props.source || "OpenStreetMap")}`,
      );
    },
  }).addTo(map);
  const proposedStationsLayer = L.geoJSON(null, {
    pointToLayer: (_feature, latlng) => L.marker(latlng, {
      icon: L.divIcon({
        className: "proposed-marker",
        html: '<span style="display:block;width:13px;height:13px;background:#9c7bd1;border:2px solid #fff;border-radius:3px;box-shadow:0 1px 5px #111"></span>',
        iconSize: [13, 13],
        iconAnchor: [6, 6],
      }),
    }),
    onEachFeature: (feature, layer) => layer.bindPopup(
      `<strong>${escapeHtml(feature.properties.name || "Proposed station")}</strong><br>` +
      "Hypothetical scenario; not an existing facility",
    ),
  }).addTo(map);
  const buffer3Layer = L.geoJSON(null, {
    style: { color: "#3d8da5", weight: 1.3, fillColor: "#59a6bd", fillOpacity: 0.2 },
  });
  const buffer5Layer = L.geoJSON(null, {
    style: { color: "#bd842e", weight: 1.3, fillColor: "#e6aa54", fillOpacity: 0.17 },
  });
  const routeLayer = L.geoJSON(null, {
    style: { color: "#e45045", weight: 5, opacity: 0.92 },
  }).addTo(map);
  const incidentLayer = L.geoJSON(null, {
    pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
      radius: 8,
      color: "#fff",
      weight: 2,
      fillColor: "#df4545",
      fillOpacity: 1,
    }),
    onEachFeature: (_feature, layer) => layer.bindPopup("<strong>Simulated incident</strong>"),
  }).addTo(map);

  L.control.layers(
    { "OpenStreetMap": baseLayer },
    {
      "Study boundary": boundaryLayer,
      "Road network": roadsLayer,
      "Synthetic samples": samplesLayer,
      "OSM-mapped stations": realStationsLayer,
      "Proposed stations": proposedStationsLayer,
      "3 km buffers": buffer3Layer,
      "5 km buffers": buffer5Layer,
      "Selected route": routeLayer,
      "Incident": incidentLayer,
    },
    { collapsed: true },
  ).addTo(map);

  const layerTogglePairs = [
    ["toggle-roads", roadsLayer],
    ["toggle-samples", samplesLayer],
    ["toggle-stations", realStationsLayer],
    ["toggle-proposals", proposedStationsLayer],
    ["toggle-buffer-3", buffer3Layer],
    ["toggle-buffer-5", buffer5Layer],
  ];

  let mode = null;
  let realStationCount = 0;
  let sampleTotal = 0;
  let proposedPoints = [];
  let mapReady = false;
  let routeController = null;
  let selectedIncident = null;

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);
  }

  async function api(path, body) {
    const options = body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        };
    const response = await fetch(path, options);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || `Request failed (${response.status}).`);
    }
    return payload;
  }

  function setStatus(message, state) {
    statusText.textContent = message;
    statusDot.classList.toggle("ready", state === "ready");
    statusDot.classList.toggle("error", state === "error");
  }

  function showError(message) {
    errorBanner.textContent = message;
    errorBanner.hidden = false;
  }

  function proposedPayload() {
    return proposedPoints.map(({ lat, lng }) => ({ lon: lng, lat }));
  }

  function setMode(nextMode) {
    mode = mode === nextMode ? null : nextMode;
    document.getElementById("incident-mode").classList.toggle("active", mode === "incident");
    document.getElementById("proposal-mode").classList.toggle("active", mode === "proposal");
    hint.textContent = mode === "incident"
      ? "Click the map to set an incident and request OSRM route comparisons."
      : mode === "proposal"
        ? "Click inside the boundary; confirm to add a hypothetical station."
        : "Choose a mode, then click inside the study boundary.";
    map.getContainer().style.cursor = mode ? "crosshair" : "";
  }

  function featureCollectionForPoints(points, category) {
    return {
      type: "FeatureCollection",
      features: points.map((point, index) => ({
        type: "Feature",
        properties: {
          station_id: `proposed-${index + 1}`,
          name: `Proposed station ${index + 1}`,
          category,
          source: "User-created simulation",
          verification_status: "Hypothetical location; not an existing facility",
        },
        geometry: { type: "Point", coordinates: [point.lng, point.lat] },
      })),
    };
  }

  function updateProposalList() {
    proposalCount.textContent = `${proposedPoints.length} proposed`;
    proposalList.replaceChildren();
    proposedPoints.forEach((point, index) => {
      const row = document.createElement("div");
      row.className = "proposal-entry";
      const label = document.createElement("span");
      label.textContent = `Proposal ${index + 1} · ${point.lat.toFixed(4)}, ${point.lng.toFixed(4)}`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "remove-proposal";
      remove.setAttribute("aria-label", `Remove proposed station ${index + 1}`);
      remove.textContent = "Remove";
      remove.addEventListener("click", async () => {
        const [removedPoint] = proposedPoints.splice(index, 1);
        updateProposalList();
        if (!(await refreshScenario())) {
          proposedPoints.splice(index, 0, removedPoint);
          updateProposalList();
        }
      });
      row.append(label, remove);
      proposalList.append(row);
    });
    proposedStationsLayer.clearLayers();
    proposedStationsLayer.addData(featureCollectionForPoints(proposedPoints, "PROPOSED"));
  }

  function updateCoverage(before, after) {
    for (const radius of [3000, 5000]) {
      const row = document.querySelector(`.coverage-row[data-radius="${radius}"]`);
      const baseline = before[String(radius)];
      const scenario = after[String(radius)];
      row.querySelector(".before-value").textContent =
        `${baseline.covered_samples}/${baseline.total_samples} · ${baseline.percentage.toFixed(1)}%`;
      row.querySelector(".after-value").textContent =
        `${scenario.covered_samples}/${scenario.total_samples} · ${scenario.percentage.toFixed(1)}%`;
    }
  }

  async function refreshScenario() {
    if (!mapReady) return false;
    try {
      const result = await api("/api/coverage", { proposed_stations: proposedPayload() });
      updateCoverage(initialCoverage, result.coverage);
      if (routeController) routeController.abort();
      routeController = null;
      selectedIncident = null;
      routeLayer.clearLayers();
      incidentLayer.clearLayers();
      routeResults.replaceChildren();
      routeStatus.textContent = "Scenario updated. Select an incident to compare routes again.";
      realStationCount = result.real_station_count;
      proposedStationsLayer.clearLayers();
      const realFeatures = result.stations.features.filter(
        (feature) => feature.properties.category === "PROPOSED",
      );
      proposedStationsLayer.addData({ type: "FeatureCollection", features: realFeatures });
      buffer3Layer.clearLayers().addData(result.buffers["3000"]);
      buffer5Layer.clearLayers().addData(result.buffers["5000"]);
      renderLayerToggles();
      setStatus(`${realStationCount} OSM-mapped · ${result.proposed_station_count} proposed`, "ready");
      return true;
    } catch (error) {
      showError(error.message);
      return false;
    }
  }

  function renderLayerToggles() {
    for (const [id, layer] of layerTogglePairs) {
      const input = document.getElementById(id);
      if (input.checked && !map.hasLayer(layer)) map.addLayer(layer);
      if (!input.checked && map.hasLayer(layer)) map.removeLayer(layer);
    }
  }

  map.on("overlayadd", (event) => {
    const entry = layerTogglePairs.find(([, layer]) => layer === event.layer);
    if (entry) document.getElementById(entry[0]).checked = true;
  });
  map.on("overlayremove", (event) => {
    const entry = layerTogglePairs.find(([, layer]) => layer === event.layer);
    if (entry) document.getElementById(entry[0]).checked = false;
  });

  function renderRouteResults(result) {
    routeResults.replaceChildren();
    const fastest = result.fastest_station_id;
    const sorted = [...result.routes].sort((a, b) => {
      if (a.success !== b.success) return a.success ? -1 : 1;
      return (a.duration_seconds ?? Infinity) - (b.duration_seconds ?? Infinity);
    });
    for (const route of sorted) {
      const card = document.createElement("div");
      card.className = `route-entry${route.station_id === fastest ? " fastest" : ""}`;
      const name = document.createElement("div");
      name.className = "route-name";
      const station = document.createElement("span");
      station.textContent = route.station_name;
      const tag = document.createElement("span");
      tag.textContent = route.station_id === fastest ? "FASTEST RETURNED" : route.success ? "ROUTE OK" : "UNAVAILABLE";
      name.append(station, tag);
      card.append(name);
      const metrics = document.createElement("div");
      metrics.className = route.success ? "route-metrics" : "route-metrics route-failure";
      metrics.textContent = route.success
        ? `${(route.distance_meters / 1000).toFixed(2)} km · ${(route.duration_seconds / 60).toFixed(1)} min`
        : (route.reason || "No route returned.");
      card.append(metrics);
      if (route.success) {
        const showRoute = document.createElement("button");
        showRoute.type = "button";
        showRoute.className = "button button-quiet show-route";
        showRoute.textContent = "Show this route";
        showRoute.addEventListener("click", () => drawSelectedRoute(route));
        card.append(showRoute);
      }
      routeResults.append(card);
    }
  }

  async function drawSelectedRoute(route) {
    if (!selectedIncident) return;
    if (routeController) routeController.abort();
    const controller = new AbortController();
    routeController = controller;
    routeStatus.textContent = `Requesting route from ${route.station_name}…`;
    try {
      const response = await fetch("/api/route", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          incident: { lon: selectedIncident.lng, lat: selectedIncident.lat },
          proposed_stations: proposedPayload(),
          selected_station_id: route.station_id,
        }),
        signal: controller.signal,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `Route request failed (${response.status}).`);
      routeLayer.clearLayers();
      if (result.geometry) {
        routeLayer.addData({ type: "Feature", properties: {}, geometry: result.geometry });
        routeStatus.textContent = `Showing route from ${route.station_name}. ${result.message}`;
      } else {
        routeStatus.textContent = result.message;
      }
    } catch (error) {
      if (error.name !== "AbortError") {
        routeStatus.textContent = error.message;
        showError(error.message);
      }
    } finally {
      if (routeController === controller) routeController = null;
    }
  }

  async function selectIncident(latlng) {
    selectedIncident = latlng;
    incidentLayer.clearLayers();
    incidentLayer.addData({
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        properties: {},
        geometry: { type: "Point", coordinates: [latlng.lng, latlng.lat] },
      }],
    });
    routeLayer.clearLayers();
    routeResults.replaceChildren();
    routeStatus.textContent = "Requesting OSRM routes…";
    setMode(null);
    if (routeController) routeController.abort();
    const controller = new AbortController();
    routeController = controller;
    document.getElementById("incident-mode").disabled = true;
    try {
      const response = await fetch("/api/route", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          incident: { lon: latlng.lng, lat: latlng.lat },
          proposed_stations: proposedPayload(),
        }),
        signal: controller.signal,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `Routing request failed (${response.status}).`);
      routeStatus.textContent = result.message;
      renderRouteResults(result);
      if (result.geometry) {
        routeLayer.addData({ type: "Feature", properties: {}, geometry: result.geometry });
        map.fitBounds(routeLayer.getBounds().pad(0.12), { maxZoom: 15 });
      }
    } catch (error) {
      if (error.name !== "AbortError") {
        routeStatus.textContent = error.message;
        showError(error.message);
      }
    } finally {
      document.getElementById("incident-mode").disabled = false;
      if (routeController === controller) routeController = null;
    }
  }

  map.on("click", async (event) => {
    if (!mode) return;
    if (mode === "incident") {
      await selectIncident(event.latlng);
      return;
    }
    if (mode === "proposal") {
      const confirmed = window.confirm(
        "Add a hypothetical proposed fire station at this map location? This will not be labeled as an existing facility.",
      );
      setMode(null);
      if (!confirmed) return;
      const newPoint = { lat: event.latlng.lat, lng: event.latlng.lng };
      proposedPoints.push(newPoint);
      updateProposalList();
      if (!(await refreshScenario())) {
        proposedPoints.pop();
        updateProposalList();
      }
    }
  });

  let initialCoverage = null;
  async function initialize() {
    try {
      const data = await api("/api/map-data");
      boundaryLayer.addData(data.boundary);
      roadsLayer.addData(data.roads);
      samplesLayer.addData(data.samples);
      realStationsLayer.addData(data.stations);
      buffer3Layer.addData(data.buffers["3000"]);
      buffer5Layer.addData(data.buffers["5000"]);
      initialCoverage = data.coverage;
      realStationCount = data.real_station_count;
      sampleTotal = data.coverage["3000"].total_samples;
      updateCoverage(initialCoverage, initialCoverage);
      document.getElementById("sample-count").textContent =
        `${sampleTotal} synthetic samples · ${realStationCount} OSM-mapped stations; OSM entries are not authority-verified.`;
      mapReady = true;
      renderLayerToggles();
      updateProposalList();
      if (boundaryLayer.getBounds().isValid()) map.fitBounds(boundaryLayer.getBounds(), { padding: [12, 12] });
      setStatus(`${realStationCount} OSM-mapped · 0 proposed`, "ready");
    } catch (error) {
      setStatus("Local GIS data unavailable", "error");
      showError(`${error.message} Prepare the local layers using the command in README, then reload this page.`);
    }
  }

  document.getElementById("incident-mode").addEventListener("click", () => setMode("incident"));
  document.getElementById("proposal-mode").addEventListener("click", () => setMode("proposal"));
  document.getElementById("reset-proposals").addEventListener("click", async () => {
    const previousPoints = proposedPoints;
    proposedPoints = [];
    updateProposalList();
    if (!(await refreshScenario())) {
      proposedPoints = previousPoints;
      updateProposalList();
    }
  });
  for (const id of [
    "toggle-roads",
    "toggle-samples",
    "toggle-stations",
    "toggle-proposals",
    "toggle-buffer-3",
    "toggle-buffer-5",
  ]) {
    document.getElementById(id).addEventListener("change", renderLayerToggles);
  }

  initialize();
})();
