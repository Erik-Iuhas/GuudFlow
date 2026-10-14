// Seeded mulberry32 generator. All game randomness flows through here and is
// passed in explicitly, so runs and tests are reproducible (decision D1).

const GOLDEN = 0x6d2b79f5;
const FORK_SALT = 0x9e3779b9;

/** Coerce any seed to an unsigned 32-bit integer. Strings are hashed (FNV-1a). */
export function toSeed(seed) {
  if (typeof seed === 'string') {
    let h = 0x811c9dc5;
    for (let i = 0; i < seed.length; i++) {
      h ^= seed.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    return h >>> 0;
  }
  const n = Number(seed);
  return Number.isFinite(n) ? Math.trunc(n) >>> 0 : 0;
}

export function createRng(seed = 0) {
  const initial = toSeed(seed);
  let state = initial;

  // Next raw unsigned 32-bit output of mulberry32.
  function u32() {
    state = (state + GOLDEN) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return (t ^ (t >>> 14)) >>> 0;
  }

  const rng = {
    seed: initial,

    /** Float in [0, 1). */
    next() {
      return u32() / 4294967296;
    },

    /** Integer in [0, n). n must be a positive integer. */
    int(n) {
      if (!Number.isInteger(n) || n < 1) {
        throw new RangeError(`rng.int(n): n must be a positive integer, got ${n}`);
      }
      return Math.floor((u32() / 4294967296) * n);
    },

    /** Uniformly chosen element of a non-empty array. */
    pick(array) {
      if (!array || array.length === 0) {
        throw new RangeError('rng.pick(array): array must be non-empty');
      }
      return array[rng.int(array.length)];
    },

    /** In-place Fisher-Yates shuffle; returns the same array. */
    shuffle(array) {
      for (let i = array.length - 1; i > 0; i--) {
        const j = rng.int(i + 1);
        const tmp = array[i];
        array[i] = array[j];
        array[j] = tmp;
      }
      return array;
    },

    /**
     * Derive an independent child generator. Consumes one value from the
     * parent, so forking is deterministic and parent/child streams differ.
     */
    fork() {
      return createRng((u32() ^ FORK_SALT) >>> 0);
    },
  };

  return rng;
}
