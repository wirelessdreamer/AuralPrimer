import Ajv, { type ErrorObject } from "ajv";

import beatsSchema from "../schemas/beats.schema.json";
import tempoMapSchema from "../schemas/tempo_map.schema.json";
import sectionsSchema from "../schemas/sections.schema.json";
import eventsSchema from "../schemas/events.schema.json";
import lyricsSchema from "../schemas/lyrics.schema.json";

export interface FeatureValidationResult<T> {
  ok: boolean;
  value?: T;
  errors?: ErrorObject[];
}

const ajv = new Ajv({ allErrors: true, strict: false });

function makeValidator<T>(schema: any) {
  const validate = ajv.compile<T>(schema);
  return (json: unknown): FeatureValidationResult<T> => {
    const ok = validate(json);
    if (ok) return { ok: true, value: json as T };
    return { ok: false, errors: validate.errors ?? [] };
  };
}

// Minimal types for now (we'll flesh these out as the schema hardens)
export type BeatsFile = unknown;
export type TempoMapFile = unknown;
export type SectionsFile = unknown;
export type EventsFile = unknown;
export type LyricsFile = unknown;

export const validateBeats = makeValidator<BeatsFile>(beatsSchema as any);
export const validateTempoMap = makeValidator<TempoMapFile>(tempoMapSchema as any);
export const validateSections = makeValidator<SectionsFile>(sectionsSchema as any);
export const validateEvents = makeValidator<EventsFile>(eventsSchema as any);
export const validateLyrics = makeValidator<LyricsFile>(lyricsSchema as any);
