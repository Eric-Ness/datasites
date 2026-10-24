import type { APIRoute } from "astro";
import { site } from "../lib/site";
import { robotsTxt } from "../lib/routes";

export const GET: APIRoute = () => new Response(robotsTxt(site.domain), { headers: { "content-type": "text/plain; charset=utf-8" } });
