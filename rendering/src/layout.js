// A deterministic street layout: metadata is never used as executable code.
export const PITCH = 6.4;
const ROAD = 5.6;
const MARGIN = 9;

export function buildingHeight(lines) {
  return Math.min(29, 2.2 + Math.log2(1 + Math.max(0, Number(lines) || 0)) * 1.6);
}

function axisPosition(index) {
  return MARGIN + index * PITCH + Math.floor(index / 5) * ROAD;
}

export function buildLayout(data) {
  const districts = data.districts.map((district, index) => {
    const files = data.files.filter(file => file.district === district.id)
      .slice().sort((a, b) => a.path.localeCompare(b.path, 'en'));
    const columns = Math.max(3, Math.ceil(Math.sqrt(Math.max(1, files.length))));
    const rows = Math.max(3, Math.ceil(files.length / columns));
    return {
      ...district, index, files, columns, rows,
      width: axisPosition(columns - 1) + MARGIN,
      depth: axisPosition(rows - 1) + MARGIN,
    };
  });
  const area = districts.reduce((sum, d) => sum + (d.width + 13) * (d.depth + 13), 0);
  const rowLimit = Math.max(150, Math.sqrt(area) * 1.25);
  let x = 0, z = 0, rowDepth = 0, totalWidth = 0;
  for (const district of districts) {
    if (x > 0 && x + district.width > rowLimit) {
      x = 0; z += rowDepth + 13; rowDepth = 0;
    }
    district.x = x;
    district.z = z;
    x += district.width + 13;
    rowDepth = Math.max(rowDepth, district.depth);
    totalWidth = Math.max(totalWidth, x - 13);
  }
  const totalDepth = z + rowDepth;
  const buildings = [];
  const byId = new Map();
  for (const district of districts) {
    district.x -= totalWidth / 2;
    district.z -= totalDepth / 2;
    district.center = { x: district.x + district.width / 2, z: district.z + district.depth / 2 };
    district.files.forEach((file, index) => {
      const building = {
        file, district: district.id, districtIndex: district.index,
        x: district.x + axisPosition(index % district.columns),
        z: district.z + axisPosition(Math.floor(index / district.columns)),
        width: 3.8, depth: 3.8, height: buildingHeight(file.lines),
        index: buildings.length,
      };
      buildings.push(building);
      byId.set(file.id, building);
    });
  }
  return {
    districts, buildings, byId,
    bounds: { minX: -totalWidth / 2 - 18, maxX: totalWidth / 2 + 18,
      minZ: -totalDepth / 2 - 18, maxZ: totalDepth / 2 + 18 },
    width: totalWidth, depth: totalDepth,
    center: { x: 0, z: 0 },
  };
}

export function createCollisionIndex(buildings, cellSize = 12) {
  const cells = new Map();
  for (const building of buildings) {
    const key = `${Math.floor(building.x / cellSize)},${Math.floor(building.z / cellSize)}`;
    if (!cells.has(key)) cells.set(key, []);
    cells.get(key).push(building);
  }
  return { cells, cellSize };
}

export function canStand(x, z, index, bounds, radius = 0.8) {
  if (x < bounds.minX + radius || x > bounds.maxX - radius ||
      z < bounds.minZ + radius || z > bounds.maxZ - radius) return false;
  const cx = Math.floor(x / index.cellSize), cz = Math.floor(z / index.cellSize);
  for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
    for (const building of index.cells.get(`${cx + dx},${cz + dz}`) || []) {
      if (Math.abs(x - building.x) < building.width / 2 + radius &&
          Math.abs(z - building.z) < building.depth / 2 + radius) return false;
    }
  }
  return true;
}
