import type { LegendItem } from "../map/style";

export function Legend(props: { title: string; items: LegendItem[]; note?: string }) {
  return (
    <section className="legend" aria-label={props.title}>
      <h3>{props.title}</h3>
      <ul>
        {props.items.map((i) => (
          <li key={i.label}>
            <span className="swatch" style={{ background: i.color }} aria-hidden="true" />
            {i.label}
          </li>
        ))}
      </ul>
      {props.note ? <p className="note">{props.note}</p> : null}
    </section>
  );
}
