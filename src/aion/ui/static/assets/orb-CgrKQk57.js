import{i as e,l as t}from"./index-CDx8-O9O.js";import{N as n,U as r,at as i,b as a,f as o,ft as s,l as c,n as l,pt as u,rt as d,u as f,v as p}from"./three.module-t1RpZedE.js";var m={idle:{amp:.035,gain:0,freq:2.2,speed:.35,spin:.07,band:0,glow:.6},listening:{amp:.04,gain:.32,freq:3,speed:.8,spin:.1,band:0,glow:.85},thinking:{amp:.05,gain:0,freq:2.6,speed:1.1,spin:.55,band:1,glow:.8},speaking:{amp:.035,gain:.42,freq:3.4,speed:1.4,spin:.14,band:0,glow:.95}},h=44,g=64,_=128,v=2600,y=1.42,b=5.05,x=.17,S=.3,C=`
  uniform float uPhase;
  uniform float uAmp;
  uniform float uLevel;
  uniform float uFreq;
  uniform float uBand;
  uniform float uBandPos;
  uniform float uPulse;
  uniform float uSize;
  uniform float uPixelRatio;
  varying float vFront;
  varying float vBand;
  varying float vPole;

  float ripple(vec3 p, float f, float t) {
    return sin(p.x * f + t * 1.3) * sin(p.y * f * 1.17 - t * 0.9) * sin(p.z * f * 0.83 + t * 1.1)
      + 0.5 * sin(p.x * f * 2.1 - t * 1.7 + p.z * 1.3) * sin(p.y * f * 1.9 + t * 1.4);
  }

  void main() {
    vec3 n = normalize(position);
    float base = ripple(n, uFreq, uPhase);
    float voice = ripple(n * 1.7, uFreq * 1.6, uPhase * 1.8);
    float band = exp(-pow((n.y - uBandPos) * 5.0, 2.0)) * uBand;
    float r = 1.0 + base * uAmp + uLevel * (0.55 + 0.45 * voice) + band * 0.05 + uPulse * 0.08;
    vec4 mv = modelViewMatrix * vec4(n * r, 1.0);
    vFront = normalize(normalMatrix * n).z * 0.5 + 0.5;
    vBand = band;
    vPole = abs(n.y);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = uSize * uPixelRatio * (3.2 / -mv.z);
  }
`,w=`
  uniform vec3 uColor;
  uniform vec3 uHighlight;
  uniform float uAlpha;
  uniform float uRound;
  varying float vFront;
  varying float vBand;
  varying float vPole;

  void main() {
    if (uRound > 0.5) {
      vec2 c = gl_PointCoord - 0.5;
      if (dot(c, c) > 0.25) discard;
    }
    float depth = mix(0.18, 1.0, vFront * vFront);
    // meridians converge at the poles: fade them so the poles do not glow
    depth *= 1.0 - 0.75 * pow(vPole, 10.0);
    vec3 color = mix(uColor, uHighlight, vBand * 0.6);
    gl_FragColor = vec4(color, uAlpha * depth * (1.0 + vBand * 1.6));
  }
`;function T(){let e=[],t=(t,n)=>{e.push(Math.sin(t)*Math.cos(n),Math.cos(t),Math.sin(t)*Math.sin(n))};for(let e=1;e<h;e++){let n=e/h*Math.PI;for(let e=0;e<_;e++)t(n,e/_*Math.PI*2),t(n,(e+1)/_*Math.PI*2)}let n=_/2;for(let e=0;e<g;e++){let r=e/g*Math.PI*2;for(let e=0;e<n;e++)t(e/n*Math.PI,r),t((e+1)/n*Math.PI,r)}let r=new f;return r.setAttribute(`position`,new p(e,3)),r}function E(){let e=new Float32Array(v*3),t=Math.PI*(3-Math.sqrt(5));for(let n=0;n<v;n++){let r=1-n/2599*2,i=Math.sqrt(1-r*r),a=t*n;e.set([Math.cos(a)*i*1.03,r*1.03,Math.sin(a)*i*1.03],n*3)}let n=new f;return n.setAttribute(`position`,new c(e,3)),n}function D(e){let t=getComputedStyle(document.documentElement).getPropertyValue(e).trim();return new o(t||`#38bdf0`)}var O=class{canvas;renderer;scene=new s;camera=new d(32,1,.1,50);group=new a;wire;dots;uniforms={uPhase:{value:0},uAmp:{value:m.idle.amp},uLevel:{value:0},uFreq:{value:m.idle.freq},uBand:{value:0},uBandPos:{value:0},uPulse:{value:0},uPixelRatio:{value:1},uHighlight:{value:new o(1,1,1)}};look={...m.idle};color=new o;palette;light=!1;level=0;resize;themeWatch;reduced;sweep=0;fullBody;voice=new e;desktop=null;squash={x:1,y:1};lean=0;frame={radius:x,centre:.7,world:1};dotSize=2.2;constructor(e,t={}){this.canvas=e,this.fullBody=t.fullBody??!1,this.renderer=new l({canvas:e,antialias:!0,alpha:!0}),this.renderer.setClearColor(0,0),this.palette=this.readPalette(),this.color.copy(this.palette.idle);let r=(e,t)=>new u({vertexShader:C,fragmentShader:w,uniforms:{...this.uniforms,uColor:{value:this.color},uAlpha:{value:.5},uSize:{value:e},uRound:{value:+!!t}},transparent:!0,depthWrite:!1});this.wire=new n(T(),r(0,!1)),this.dots=new i(E(),r(2.2,!0)),this.group.add(this.wire,this.dots),this.group.rotation.set(.35,0,-.18),this.scene.add(this.group),this.applyTheme(),this.reduced=matchMedia(`(prefers-reduced-motion: reduce)`),this.resize=new ResizeObserver(()=>this.fit()),this.resize.observe(e),this.themeWatch=new MutationObserver(()=>{this.palette=this.readPalette(),this.applyTheme()}),this.themeWatch.observe(document.documentElement,{attributeFilter:[`data-theme`]}),this.fit()}readPalette(){return{idle:D(`--orb-idle`),listening:D(`--orb-listening`),thinking:D(`--orb-thinking`),speaking:D(`--orb-speaking`)}}applyTheme(){this.light=document.documentElement.dataset.theme===`light`;let e=this.light?1:2;this.uniforms.uHighlight.value.set(this.light?858938:16777215);for(let t of[this.wire,this.dots]){let n=t.material;n.blending=e,n.needsUpdate=!0}}fit(){let{width:e,height:t}=this.canvas.getBoundingClientRect();if(!e||!t)return;let n=Math.min(window.devicePixelRatio||1,2);this.renderer.setPixelRatio(n),this.renderer.setSize(e,t,!1),this.uniforms.uPixelRatio.value=n;let i=e/t;if(this.camera.aspect=i,this.fullBody){this.fitDesktop(i);return}let a=r.degToRad(this.camera.fov),o=2*Math.atan(Math.tan(a/2)*i),s=Math.min(a,o)/2;this.camera.position.set(0,0,y/Math.sin(s)*.98),this.camera.lookAt(0,0,0),this.camera.updateProjectionMatrix()}fitDesktop(e){let t=r.degToRad(this.camera.fov),n=2*b*Math.tan(t/2),i=Math.min(x,S*e),a=1-i*y-.012;this.frame={radius:i,centre:a,world:n},this.dotSize=2.2*Math.min(1,Math.max(.6,i/.345)),this.camera.position.set(0,0,b),this.camera.lookAt(0,0,0),this.camera.updateProjectionMatrix()}moveBody(e,n){let{dt:r}=e,i=this.voice.update(e,n),{radius:a,centre:o,world:s}=this.frame,c=this.desktop,l=c?.mode??`stand`,u=1,d=1;n||(l===`drag`?(u=1.06,d=.94):l===`fall`&&(d=1+Math.min(.16,Math.abs(c?.vy??0)*.14),u=1/Math.sqrt(d))),this.squash.x+=(u-this.squash.x)*t(12,r),this.squash.y+=(d-this.squash.y)*t(12,r);let f=n?0:Math.max(-1,Math.min(1,(c?.vx??0)*3));this.lean+=(f-this.lean)*t(6,r);let p=n?null:c?.look??null,m=a*s*i.scale,h=this.group;return h.scale.set(m*this.squash.x,m*this.squash.y,m),h.position.set((i.sway+(p?p[0]*.008:0))*s,(.5-o+i.lift+(p?p[1]*.006:0))*s,0),h.rotation.z=-.18+i.roll+this.lean*.25,h.rotation.y+=this.lean*r*2.2,this.uniforms.uPulse.value=Math.max(this.uniforms.uPulse.value,i.beat*.5),i.glow}update(e){if(document.hidden)return;let{state:n,outputLevel:r,inputLevel:i,dt:a}=e,o=this.reduced.matches,s=this.fullBody?this.moveBody(e,o):0,c=m[n],l=t(3,a),u=this.look;for(let e of Object.keys(u))u[e]+=(c[e]-u[e])*l;this.color.lerp(this.palette[n],t(4,a));let d=n===`speaking`?r:n===`listening`?i:0,f=d>this.level?18:5;this.level+=(d-this.level)*t(f,a);let p=+!o,h=this.uniforms;h.uPhase.value+=a*u.speed*p,h.uAmp.value=u.amp*(o?.4:1),h.uFreq.value=u.freq,h.uLevel.value=this.level*u.gain,h.uBand.value=u.band,this.sweep+=a*1.6*p,h.uBandPos.value=o?0:Math.sin(this.sweep)*.9,h.uPulse.value*=Math.exp(-a*4),this.group.rotation.y+=a*u.spin*p;let g=u.glow*(this.light?.75:.42)*(1+s*.35);this.wire.material.uniforms.uAlpha.value=g;let _=this.dots.material.uniforms;_.uAlpha.value=Math.min(1,g*1.6),_.uSize.value=this.dotSize,this.renderer.render(this.scene,this.camera)}react(e){(e===`wake`||e===`wave`)&&(this.uniforms.uPulse.value=1)}setDesktop(e){this.desktop=e}layout(){if(!this.fullBody)return null;let{width:e,height:t}=this.renderer.domElement,{radius:n,centre:r}=this.frame;return{feet:Math.min(.985,r+n*1.1+.02)*t,seat:(r+n)*t,center:e/2,head:(r-n*1.05)*t}}dispose(){this.resize.disconnect(),this.themeWatch.disconnect();for(let e of[this.wire,this.dots])e.geometry.dispose(),e.material.dispose();this.renderer.dispose(),this.renderer.forceContextLoss()}};export{O as OrbAvatar};