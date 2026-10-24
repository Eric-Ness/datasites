import type { APIRoute } from "astro";
import { faviconSvg } from "../lib/theme";
import { site } from "../lib/site";

export const GET: APIRoute = () => new Response(faviconSvg(site.id), { headers: { "content-type": "image/svg+xml" } });
