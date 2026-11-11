// Forwards /api/* from the browser to the Python API. The browser only ever talks to
// this origin, and API_URL is read at runtime, so the same build runs locally, in
// Docker Compose and on Cloud Run.
import type { NextRequest } from "next/server";

const FORWARDED_REQUEST_HEADERS = ["content-type", "range", "accept"];
const FORWARDED_RESPONSE_HEADERS = [
  "content-type",
  "content-length",
  "content-range",
  "accept-ranges",
  "etag",
  "last-modified",
];

async function forward(request: NextRequest, path: string[]): Promise<Response> {
  const base = (process.env.API_URL ?? "http://localhost:8400").replace(/\/$/, "");
  const target = `${base}/api/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const hasBody = request.method === "POST" || request.method === "PUT";
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      // Required by Node's fetch when streaming a request body.
      ...(hasBody ? { duplex: "half" } : {}),
      cache: "no-store",
      redirect: "manual",
    } as RequestInit);
  } catch {
    return Response.json(
      { detail: "The API is not reachable. Is it running on API_URL?" },
      { status: 502 },
    );
  }
  const responseHeaders = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: Context) {
  return forward(request, (await context.params).path);
}

export async function POST(request: NextRequest, context: Context) {
  return forward(request, (await context.params).path);
}

export async function DELETE(request: NextRequest, context: Context) {
  return forward(request, (await context.params).path);
}
