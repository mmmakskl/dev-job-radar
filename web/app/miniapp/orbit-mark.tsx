export default function OrbitMark({ size = 42, animated = false }: { size?: number; animated?: boolean }) {
  return <svg className={`ma-orbit-mark ${animated ? 'is-animated' : ''}`} width={size} height={size} viewBox="0 0 100 100" fill="none" aria-hidden="true">
    <circle className="ma-orbit-dash" cx="50" cy="50" r="35" stroke="#A98AF8" strokeWidth="3" strokeDasharray="5 10"/>
    <g transform="rotate(-39 50 50)">
      <ellipse className="ma-orbit-ring" cx="50" cy="50" rx="43" ry="19" stroke="#B99BFF" strokeWidth="3.5"/>
      <circle className="ma-orbit-satellite" cx="93" cy="50" r="6" fill="white">
        {animated && <animateMotion dur="3.2s" repeatCount="indefinite" path="M 0 0 A 43 19 0 1 0 -86 0 A 43 19 0 1 0 0 0"/>}
      </circle>
    </g>
    <circle cx="50" cy="50" r="12" fill="#A783F5"/>
  </svg>;
}
