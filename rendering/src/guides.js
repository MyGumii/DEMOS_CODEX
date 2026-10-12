// Curated explanations are kept outside the generated repository inventory.
// A later scan may remove a file; never leave a tour pointing at a missing tower.
export function createGuideIndex(data, packs, originalTour) {
  const fileIds = new Set(data.files.map(file => file.id));
  const districtIds = new Set(data.districts.map(district => district.id));
  const districts = new Map(), files = new Map(), tours = new Map();
  const candidates = originalTour ? [{ ...originalTour, title: '채팅 요청의 여정', districtId: 'gateway', kind: 'workflow' }] : [];
  for (const pack of packs) {
    for (const guide of pack?.districts || []) {
      if (districtIds.has(guide.id)) districts.set(guide.id, {
        ...guide, keyFiles: (guide.keyFiles || []).filter(id => fileIds.has(id)),
      });
    }
    for (const guide of pack?.files || []) if (fileIds.has(guide.fileId)) files.set(guide.fileId, guide);
    candidates.push(...(pack?.tours || []));
  }
  for (const candidate of candidates) {
    const steps = (candidate.steps || []).filter(step => fileIds.has(step.fileId));
    if (steps.length && districtIds.has(candidate.districtId)) tours.set(candidate.id, { ...candidate, steps });
  }
  return { districts, files, tours };
}

export function toursForFile(index, fileId) {
  return [...index.tours.values()].filter(tour => tour.steps.some(step => step.fileId === fileId));
}
