const fs = require('fs');
const path = require('path');
const config = require('./config');

let topology = {};

function loadTopology() {
    try {
        topology = fs.existsSync(config.TOPOLOGY_FILE)
            ? JSON.parse(fs.readFileSync(config.TOPOLOGY_FILE, 'utf8'))
            : {};
    } catch (e) {
        console.error(`${config.MY_TAG} Could not load topology file:`, e.message);
    }
    return topology;
}

function saveTopology() {
    try {
        fs.mkdirSync(path.dirname(config.TOPOLOGY_FILE), { recursive: true });
        fs.writeFileSync(config.TOPOLOGY_FILE, JSON.stringify(topology, null, 2));
    } catch (e) {
        console.error(`${config.MY_TAG} Could not save topology file:`, e.message);
    }
}

function getTopology() {
    return topology;
}

function updateTopology(nodeId, starId) {
    const key = String(nodeId);
    if (topology[key] !== String(starId)) {
        topology[key] = String(starId);
        saveTopology();
        console.log(`${config.MY_TAG} Topology: node ${nodeId} -> star ${starId}`);
    }
}

function getStarId(nodeId) {
    return topology[String(nodeId)];
}

module.exports = { 
    loadTopology, 
    saveTopology, 
    getTopology, 
    updateTopology, 
    getStarId 
};