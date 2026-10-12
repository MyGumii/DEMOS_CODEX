"use strict";

function memory() {
  const values = new Map();
  return {get length() {return values.size;}, key: i => [...values.keys()][i] ?? null,
    getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)};
}

// Model ifAvailable semantics for unit tests. Real browser locks are verified
// separately with independently loaded pages in the named browser fixture.
function lockManager() {
  const held = new Set();
  return {request: async (name, options, action) => {
    if (options.mode !== "exclusive" || options.ifAvailable !== true) throw Error("unsupported test lock options");
    if (held.has(name)) return action(null);
    held.add(name);
    try {return await action({name, mode: "exclusive"});}
    finally {held.delete(name);}
  }};
}

module.exports = {memory, lockManager};
