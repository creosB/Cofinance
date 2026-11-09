import benchmark from '@/lib/narrator-benchmark.json'
import '@/narrator-comparison.css'

// Historical measurements only: this component has no model actions or selection effects.
export function NarratorComparison(){
 return <section className="narrator-comparison" aria-label="Recorded narrator comparison">
  <h3>LFM3 remains the chosen narrator</h3>
  <p>{benchmark.suggestion} Selection stays under your control.</p>
  <details>
   <summary>Recorded smoke comparison</summary>
   <p>Measured Nov 8-9, 2026. Seven unique narrator cases, repeated twice. Not a financial-accuracy or overall quality ranking.</p>
   <div className="narrator-table-scroll" role="region" aria-label="Narrator comparison scores" tabIndex={0}>
    <table>
     <caption>Q4_K_M on CPU, llama.cpp b12110. File sizes use decimal GB, not RAM.</caption>
     <thead><tr><th scope="col">Narrator</th><th scope="col">Narration</th><th scope="col">Numbers</th><th scope="col">Median</th></tr></thead>
     <tbody>{benchmark.models.map(model=><tr key={model.id}>
      <th scope="row">{model.name}<span>{model.id===benchmark.retainedNarratorId?'Retained choice':'Candidate only'}</span></th>
      <td>{model.scores.narration.passed}/{model.scores.narration.total}</td>
      <td>{model.scores.number_faithfulness.passed}/{model.scores.number_faithfulness.total}</td>
      <td>{(model.narrationMedianMs/1000).toFixed(3)} s</td>
     </tr>)}</tbody>
    </table>
   </div>
   <ul className="narrator-speed-notes">{benchmark.models.map(model=><li key={model.id}>{model.name}: {model.effectiveTokensPerSecond.toFixed(1)} effective tokens/s, {(model.sizeBytes/1e9).toFixed(3)} GB file.</li>)}</ul>
   <p>Median is narration request time. Speed includes prompt processing across all 46 calls per arm. Background activity may affect timings. Temperature 0, thinking off, 256 output tokens, 6 CPU threads.</p>
   <p>No alternatives were enabled by this comparison. LFM3 remains the quality-first choice.</p>
  </details>
 </section>
}
