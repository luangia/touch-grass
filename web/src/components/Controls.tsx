/** Search, threshold and area-type controls: the keyboard-accessible path (lesson §5.9). */

import { useId, useState } from "react";

import type { AreaFeature } from "../api/geojson";
import { displayName, typeLabel } from "../format";
import { type Mode, MODES } from "../state/url";

export function SearchBox(props: {
  features: AreaFeature[];
  areaType: string;
  onSelect: (areaId: string) => void;
}) {
  const listId = useId();
  const [text, setText] = useState("");
  const options = props.features
    .map((f) => ({ id: f.id, label: displayName(f.properties.name, f.properties.area_type) }))
    .sort((a, b) => a.label.localeCompare(b.label));
  const choose = (value: string) => {
    setText(value);
    const hit = options.find((o) => o.label.toLowerCase() === value.trim().toLowerCase());
    if (hit) props.onSelect(hit.id);
  };
  return (
    <label className="control">
      <span>Find a {typeLabel(props.areaType).toLowerCase()}</span>
      <input
        type="search"
        list={listId}
        value={text}
        placeholder="Start typing a name"
        onChange={(e) => choose(e.target.value)}
        aria-describedby={`${listId}-hint`}
      />
      <datalist id={listId}>
        {options.map((o) => (
          <option key={o.id} value={o.label} />
        ))}
      </datalist>
      <small id={`${listId}-hint`}>{options.length} to choose from</small>
    </label>
  );
}

export function ThresholdToggle(props: {
  thresholds: { minutes: number; meters: number }[];
  value: number;
  onChange: (minutes: number) => void;
}) {
  return (
    <fieldset className="control segmented">
      <legend>Walk time</legend>
      {props.thresholds.map((t) => (
        <label key={t.minutes}>
          <input
            type="radio"
            name="threshold"
            value={t.minutes}
            checked={t.minutes === props.value}
            onChange={() => props.onChange(t.minutes)}
          />
          <span>{t.minutes} min</span>
        </label>
      ))}
    </fieldset>
  );
}

export function AreaTypeToggle(props: {
  types: { area_type: string; areas: number }[];
  value: string;
  onChange: (areaType: string) => void;
}) {
  if (props.types.length < 2) return null;
  return (
    <fieldset className="control segmented">
      <legend>Areas</legend>
      {props.types.map((t) => (
        <label key={t.area_type}>
          <input
            type="radio"
            name="area-type"
            value={t.area_type}
            checked={t.area_type === props.value}
            onChange={() => props.onChange(t.area_type)}
          />
          <span>
            {typeLabel(t.area_type, true)} ({t.areas})
          </span>
        </label>
      ))}
    </fieldset>
  );
}

const MODE_LABELS: Record<Mode, string> = {
  residents: "Residents near a park",
  blocks: "Walk from each block",
};

/** The two questions the map answers, one at a time (D-024 amendment). */
export function ModeToggle(props: { value: Mode; onChange: (mode: Mode) => void }) {
  return (
    <fieldset className="control segmented">
      <legend>Show</legend>
      {MODES.map((m) => (
        <label key={m}>
          <input
            type="radio"
            name="mode"
            value={m}
            checked={m === props.value}
            onChange={() => props.onChange(m)}
          />
          <span>{MODE_LABELS[m]}</span>
        </label>
      ))}
    </fieldset>
  );
}
