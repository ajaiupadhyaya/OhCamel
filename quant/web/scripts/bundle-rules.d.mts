// Types for bundle-rules.mjs (plain ESM run by node, so the test that imports it needs declarations).

/** One entry of Vite's dist/.vite/manifest.json, as far as the bundle guard reads it. */
export interface ManifestChunk {
  file: string;
  src?: string;
  name?: string;
  isEntry?: boolean;
  isDynamicEntry?: boolean;
  imports?: string[];
  dynamicImports?: string[];
}

/** The manifest: chunk key (the source path, or `_name-hash.js` for a shared chunk) to chunk. */
export type Manifest = Record<string, ManifestChunk>;

export const MACHINE_DIR: string;
export const MACHINE_PAGE: string;
export function isMachineSource(src: string | undefined): boolean;
export function isThree(key: string, chunk: ManifestChunk): boolean;
export function staticClosure(manifest: Manifest, entryKey: string): Set<string>;
export function threeManifestFailures(manifest: Manifest, entryKey: string): string[];
export function threeSourceFailures(rel: string, text: string): string[];
