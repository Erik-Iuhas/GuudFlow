export interface Avatar {
  gradient: string;
  initials: string;
}

// FNV-1a 32-bit for environments without SubtleCrypto
function fnv1a(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h * 16777619) >>> 0;
  }
  return h;
}

function hexColor(n: number): string {
  return "#" + (n & 0xffffff).toString(16).padStart(6, "0");
}

function computeGradientSync(seed: string): string {
  const h1 = fnv1a(seed);
  const h2 = fnv1a(seed + seed);
  return `linear-gradient(135deg, ${hexColor(h1)} 0%, ${hexColor(h2)} 100%)`;
}

function computeInitials(seed: string): string {
  const parts = seed.split(/[\s_-]+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return seed.slice(0, 2).toUpperCase();
}

const _cache = new Map<string, Avatar>();

export function avatarFor(seed: string): Avatar {
  if (_cache.has(seed)) return _cache.get(seed)!;
  const av: Avatar = {
    gradient: computeGradientSync(seed),
    initials: computeInitials(seed),
  };
  _cache.set(seed, av);
  return av;
}
