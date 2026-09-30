import { BrandMark } from "@/components/BrandLogo";

const particles = Array.from({ length: 14 }, (_, index) => index);

export default function MemoryEcosystem() {
  return (
    <div className="memory-ecosystem" aria-label="Sources activated by specialist agents and compiled into context">
      <div className="ecosystem-grid" aria-hidden="true" />
      <div className="source-cloud" aria-hidden="true">
        <span className="source-chip github">GH<small>GitHub</small></span>
        <span className="source-chip slack">SL<small>Slack</small></span>
        <span className="source-chip docs">DC<small>Docs</small></span>
        <span className="source-chip issues">IS<small>Issues</small></span>
        {particles.map((particle) => <i className={`source-particle p${particle + 1}`} key={particle} />)}
      </div>

      <div className="activation-stream stream-in" aria-hidden="true">
        <i /><i /><i /><i /><i />
      </div>

      <div className="swarm-core">
        <div className="swarm-aura" aria-hidden="true"><i /><i /><i /></div>
        <div className="swarm-nucleus">
          <BrandMark />
          <small>context</small>
          <strong>SWARM</strong>
        </div>
        <span className="agent-orbit sensory"><i />Sensory</span>
        <span className="agent-orbit forager"><i />Forager</span>
        <span className="agent-orbit historian"><i />Historian</span>
      </div>

      <div className="activation-stream stream-out" aria-hidden="true">
        <i /><i /><i /><i /><i />
      </div>

      <div className="compiled-context">
        <div className="compiled-head">
          <span><i /> Context compiled</span>
          <small>1,284 / 6,000 tokens</small>
        </div>
        <div className="compiled-query">What changed in payments?</div>
        <div className="compiled-source active"><b>S1</b><span>Current policy</span><i>96%</i></div>
        <div className="compiled-source"><b>S2</b><span>Service graph</span><i>91%</i></div>
        <div className="compiled-source"><b>S3</b><span>Recent decision</span><i>87%</i></div>
        <div className="compiled-foot"><span>3/3 agents</span><span>scope verified</span></div>
      </div>
    </div>
  );
}
