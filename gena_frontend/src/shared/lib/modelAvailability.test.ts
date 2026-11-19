import { describe, expect, it } from 'vitest';
import { pickModelId, selectableModels, toHealthMap } from './modelAvailability';

const models = [
  { id: 'up', name: 'Up' },
  { id: 'down', name: 'Down' },
  { id: 'unknown', name: 'Unknown' },
];

describe('toHealthMap', () => {
  it('maps model id to its availability', () => {
    expect(
      toHealthMap([
        { id: 'up', name: 'Up', available: true },
        { id: 'down', name: 'Down', available: false, reason: 'not served' },
      ]),
    ).toEqual({ up: true, down: false });
  });

  it('is empty while health is unknown', () => {
    expect(toHealthMap(undefined)).toEqual({});
  });
});

describe('selectableModels', () => {
  it('hides models the health probe reports down and keeps up and unknown ones', () => {
    expect(selectableModels(models, { up: true, down: false }).map((m) => m.id)).toEqual(['up', 'unknown']);
  });

  it('keeps every model while health is unknown (not loaded or failed)', () => {
    expect(selectableModels(models, {}).map((m) => m.id)).toEqual(['up', 'down', 'unknown']);
  });
});

describe('pickModelId', () => {
  it('takes the first preferred id that is still selectable', () => {
    expect(pickModelId(models, 'gone', 'unknown')).toBe('unknown');
  });

  it('falls back to the first selectable model', () => {
    expect(pickModelId(models, '', null, undefined, 'gone')).toBe('up');
  });

  it('is empty when nothing is selectable', () => {
    expect(pickModelId([], 'up')).toBe('');
  });
});
