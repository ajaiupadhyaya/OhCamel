/**
 * Whether this browser can draw the machine. It lives apart from the scene so that asking does not load three.js: a
 * browser that cannot draw gets the list, and never downloads what it could not use.
 */
export function webglAvailable(): boolean {
  try {
    const gl = document.createElement("canvas").getContext("webgl2");
    gl?.getExtension("WEBGL_lose_context")?.loseContext();
    return gl !== null;
  } catch {
    return false;
  }
}
