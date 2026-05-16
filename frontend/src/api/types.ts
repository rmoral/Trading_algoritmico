// Thin aliases over the auto-generated OpenAPI schema in
// `schema.gen.ts`. The generator is invoked by `npm run gen:api`
// (which itself calls `scripts/export_openapi.py` to refresh
// `openapi.json` from the FastAPI app). Hand-edit this file ONLY to
// add new aliases or compositions; the underlying types live in
// `schema.gen.ts` and must not be touched by hand.

import type { components } from "./schema.gen";

type Schemas = components["schemas"];

export type UserResponse = Schemas["UserResponse"];
export type OpenPositionResponse = Schemas["OpenPositionResponse"];
export type PnLResponse = Schemas["PnLResponse"];
export type BotStatusResponse = Schemas["BotStatusResponse"];
export type ConfigPolicyResponse = Schemas["ConfigPolicyResponse"];
export type ActiveAssetResponse = Schemas["ActiveAssetResponse"];
export type TOTPEnrollResponse = Schemas["TOTPEnrollResponse"];

export type ConnectionState = BotStatusResponse["connection_state"];
