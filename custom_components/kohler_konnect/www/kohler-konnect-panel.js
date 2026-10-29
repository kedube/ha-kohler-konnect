// node_modules/@lit/reactive-element/css-tag.js
var e = globalThis, t = e.ShadowRoot && (e.ShadyCSS === void 0 || e.ShadyCSS.nativeShadow) && "adoptedStyleSheets" in Document.prototype && "replace" in CSSStyleSheet.prototype, n = Symbol(), r = /* @__PURE__ */ new WeakMap(), i = class {
	constructor(e, t, r) {
		if (this._$cssResult$ = !0, r !== n) throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");
		this.cssText = e, this.t = t;
	}
	get styleSheet() {
		let e = this.o, n = this.t;
		if (t && e === void 0) {
			let t = n !== void 0 && n.length === 1;
			t && (e = r.get(n)), e === void 0 && ((this.o = e = new CSSStyleSheet()).replaceSync(this.cssText), t && r.set(n, e));
		}
		return e;
	}
	toString() {
		return this.cssText;
	}
}, a = (e) => new i(typeof e == "string" ? e : e + "", void 0, n), o = (e, ...t) => new i(e.length === 1 ? e[0] : t.reduce((t, n, r) => t + ((e) => {
	if (!0 === e._$cssResult$) return e.cssText;
	if (typeof e == "number") return e;
	throw Error("Value passed to 'css' function must be a 'css' function result: " + e + ". Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.");
})(n) + e[r + 1], e[0]), e, n), s = (n, r) => {
	if (t) n.adoptedStyleSheets = r.map((e) => e instanceof CSSStyleSheet ? e : e.styleSheet);
	else for (let t of r) {
		let r = document.createElement("style"), i = e.litNonce;
		i !== void 0 && r.setAttribute("nonce", i), r.textContent = t.cssText, n.appendChild(r);
	}
}, c = t ? (e) => e : (e) => e instanceof CSSStyleSheet ? ((e) => {
	let t = "";
	for (let n of e.cssRules) t += n.cssText;
	return a(t);
})(e) : e, { is: l, defineProperty: u, getOwnPropertyDescriptor: ee, getOwnPropertyNames: te, getOwnPropertySymbols: ne, getPrototypeOf: re } = Object, d = globalThis, ie = d.trustedTypes, ae = ie ? ie.emptyScript : "", oe = d.reactiveElementPolyfillSupport, f = (e, t) => e, se = {
	toAttribute(e, t) {
		switch (t) {
			case Boolean:
				e = e ? ae : null;
				break;
			case Object:
			case Array: e = e == null ? e : JSON.stringify(e);
		}
		return e;
	},
	fromAttribute(e, t) {
		let n = e;
		switch (t) {
			case Boolean:
				n = e !== null;
				break;
			case Number:
				n = e === null ? null : Number(e);
				break;
			case Object:
			case Array: try {
				n = JSON.parse(e);
			} catch {
				n = null;
			}
		}
		return n;
	}
}, ce = (e, t) => !l(e, t), le = {
	attribute: !0,
	type: String,
	converter: se,
	reflect: !1,
	useDefault: !1,
	hasChanged: ce
};
Symbol.metadata ??= Symbol("metadata"), d.litPropertyMetadata ??= /* @__PURE__ */ new WeakMap();
var p = class extends HTMLElement {
	static addInitializer(e) {
		this._$Ei(), (this.l ??= []).push(e);
	}
	static get observedAttributes() {
		return this.finalize(), this._$Eh && [...this._$Eh.keys()];
	}
	static createProperty(e, t = le) {
		if (t.state && (t.attribute = !1), this._$Ei(), this.prototype.hasOwnProperty(e) && ((t = Object.create(t)).wrapped = !0), this.elementProperties.set(e, t), !t.noAccessor) {
			let n = Symbol(), r = this.getPropertyDescriptor(e, n, t);
			r !== void 0 && u(this.prototype, e, r);
		}
	}
	static getPropertyDescriptor(e, t, n) {
		let { get: r, set: i } = ee(this.prototype, e) ?? {
			get() {
				return this[t];
			},
			set(e) {
				this[t] = e;
			}
		};
		return {
			get: r,
			set(t) {
				let a = r?.call(this);
				i?.call(this, t), this.requestUpdate(e, a, n);
			},
			configurable: !0,
			enumerable: !0
		};
	}
	static getPropertyOptions(e) {
		return this.elementProperties.get(e) ?? le;
	}
	static _$Ei() {
		if (this.hasOwnProperty(f("elementProperties"))) return;
		let e = re(this);
		e.finalize(), e.l !== void 0 && (this.l = [...e.l]), this.elementProperties = new Map(e.elementProperties);
	}
	static finalize() {
		if (this.hasOwnProperty(f("finalized"))) return;
		if (this.finalized = !0, this._$Ei(), this.hasOwnProperty(f("properties"))) {
			let e = this.properties, t = [...te(e), ...ne(e)];
			for (let n of t) this.createProperty(n, e[n]);
		}
		let e = this[Symbol.metadata];
		if (e !== null) {
			let t = litPropertyMetadata.get(e);
			if (t !== void 0) for (let [e, n] of t) this.elementProperties.set(e, n);
		}
		this._$Eh = /* @__PURE__ */ new Map();
		for (let [e, t] of this.elementProperties) {
			let n = this._$Eu(e, t);
			n !== void 0 && this._$Eh.set(n, e);
		}
		this.elementStyles = this.finalizeStyles(this.styles);
	}
	static finalizeStyles(e) {
		let t = [];
		if (Array.isArray(e)) {
			let n = new Set(e.flat(1 / 0).reverse());
			for (let e of n) t.unshift(c(e));
		} else e !== void 0 && t.push(c(e));
		return t;
	}
	static _$Eu(e, t) {
		let n = t.attribute;
		return !1 === n ? void 0 : typeof n == "string" ? n : typeof e == "string" ? e.toLowerCase() : void 0;
	}
	constructor() {
		super(), this._$Ep = void 0, this.isUpdatePending = !1, this.hasUpdated = !1, this._$Em = null, this._$Ev();
	}
	_$Ev() {
		this._$ES = new Promise((e) => this.enableUpdating = e), this._$AL = /* @__PURE__ */ new Map(), this._$E_(), this.requestUpdate(), this.constructor.l?.forEach((e) => e(this));
	}
	addController(e) {
		(this._$EO ??= /* @__PURE__ */ new Set()).add(e), this.renderRoot !== void 0 && this.isConnected && e.hostConnected?.();
	}
	removeController(e) {
		this._$EO?.delete(e);
	}
	_$E_() {
		let e = /* @__PURE__ */ new Map(), t = this.constructor.elementProperties;
		for (let n of t.keys()) this.hasOwnProperty(n) && (e.set(n, this[n]), delete this[n]);
		e.size > 0 && (this._$Ep = e);
	}
	createRenderRoot() {
		let e = this.shadowRoot ?? this.attachShadow(this.constructor.shadowRootOptions);
		return s(e, this.constructor.elementStyles), e;
	}
	connectedCallback() {
		this.renderRoot ??= this.createRenderRoot(), this.enableUpdating(!0), this._$EO?.forEach((e) => e.hostConnected?.());
	}
	enableUpdating(e) {}
	disconnectedCallback() {
		this._$EO?.forEach((e) => e.hostDisconnected?.());
	}
	attributeChangedCallback(e, t, n) {
		this._$AK(e, n);
	}
	_$ET(e, t) {
		let n = this.constructor.elementProperties.get(e), r = this.constructor._$Eu(e, n);
		if (r !== void 0 && !0 === n.reflect) {
			let i = (n.converter?.toAttribute === void 0 ? se : n.converter).toAttribute(t, n.type);
			this._$Em = e, i == null ? this.removeAttribute(r) : this.setAttribute(r, i), this._$Em = null;
		}
	}
	_$AK(e, t) {
		let n = this.constructor, r = n._$Eh.get(e);
		if (r !== void 0 && this._$Em !== r) {
			let e = n.getPropertyOptions(r), i = typeof e.converter == "function" ? { fromAttribute: e.converter } : e.converter?.fromAttribute === void 0 ? se : e.converter;
			this._$Em = r;
			let a = i.fromAttribute(t, e.type);
			this[r] = a ?? this._$Ej?.get(r) ?? a, this._$Em = null;
		}
	}
	requestUpdate(e, t, n, r = !1, i) {
		if (e !== void 0) {
			let a = this.constructor;
			if (!1 === r && (i = this[e]), n ??= a.getPropertyOptions(e), !((n.hasChanged ?? ce)(i, t) || n.useDefault && n.reflect && i === this._$Ej?.get(e) && !this.hasAttribute(a._$Eu(e, n)))) return;
			this.C(e, t, n);
		}
		!1 === this.isUpdatePending && (this._$ES = this._$EP());
	}
	C(e, t, { useDefault: n, reflect: r, wrapped: i }, a) {
		n && !(this._$Ej ??= /* @__PURE__ */ new Map()).has(e) && (this._$Ej.set(e, a ?? t ?? this[e]), !0 !== i || a !== void 0) || (this._$AL.has(e) || (this.hasUpdated || n || (t = void 0), this._$AL.set(e, t)), !0 === r && this._$Em !== e && (this._$Eq ??= /* @__PURE__ */ new Set()).add(e));
	}
	async _$EP() {
		this.isUpdatePending = !0;
		try {
			await this._$ES;
		} catch (e) {
			Promise.reject(e);
		}
		let e = this.scheduleUpdate();
		return e != null && await e, !this.isUpdatePending;
	}
	scheduleUpdate() {
		return this.performUpdate();
	}
	performUpdate() {
		if (!this.isUpdatePending) return;
		if (!this.hasUpdated) {
			if (this.renderRoot ??= this.createRenderRoot(), this._$Ep) {
				for (let [e, t] of this._$Ep) this[e] = t;
				this._$Ep = void 0;
			}
			let e = this.constructor.elementProperties;
			if (e.size > 0) for (let [t, n] of e) {
				let { wrapped: e } = n, r = this[t];
				!0 !== e || this._$AL.has(t) || r === void 0 || this.C(t, void 0, n, r);
			}
		}
		let e = !1, t = this._$AL;
		try {
			e = this.shouldUpdate(t), e ? (this.willUpdate(t), this._$EO?.forEach((e) => e.hostUpdate?.()), this.update(t)) : this._$EM();
		} catch (t) {
			throw e = !1, this._$EM(), t;
		}
		e && this._$AE(t);
	}
	willUpdate(e) {}
	_$AE(e) {
		this._$EO?.forEach((e) => e.hostUpdated?.()), this.hasUpdated || (this.hasUpdated = !0, this.firstUpdated(e)), this.updated(e);
	}
	_$EM() {
		this._$AL = /* @__PURE__ */ new Map(), this.isUpdatePending = !1;
	}
	get updateComplete() {
		return this.getUpdateComplete();
	}
	getUpdateComplete() {
		return this._$ES;
	}
	shouldUpdate(e) {
		return !0;
	}
	update(e) {
		this._$Eq &&= this._$Eq.forEach((e) => this._$ET(e, this[e])), this._$EM();
	}
	updated(e) {}
	firstUpdated(e) {}
};
p.elementStyles = [], p.shadowRootOptions = { mode: "open" }, p[f("elementProperties")] = /* @__PURE__ */ new Map(), p[f("finalized")] = /* @__PURE__ */ new Map(), oe?.({ ReactiveElement: p }), (d.reactiveElementVersions ??= []).push("2.1.2");
// node_modules/lit-html/lit-html.js
var ue = globalThis, de = (e) => e, fe = ue.trustedTypes, pe = fe ? fe.createPolicy("lit-html", { createHTML: (e) => e }) : void 0, me = "$lit$", m = `lit$${Math.random().toFixed(9).slice(2)}$`, he = "?" + m, ge = `<${he}>`, h = document, g = () => h.createComment(""), _ = (e) => e === null || typeof e != "object" && typeof e != "function", _e = Array.isArray, ve = (e) => _e(e) || typeof e?.[Symbol.iterator] == "function", ye = "[ 	\n\f\r]", v = /<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g, be = /-->/g, xe = />/g, y = RegExp(`>|${ye}(?:([^\\s"'>=/]+)(${ye}*=${ye}*(?:[^ \t\n\f\r"'\`<>=]|("|')|))|$)`, "g"), Se = /'/g, Ce = /"/g, we = /^(?:script|style|textarea|title)$/i, Te = (e) => (t, ...n) => ({
	_$litType$: e,
	strings: t,
	values: n
}), b = Te(1), x = Te(2), S = Symbol.for("lit-noChange"), C = Symbol.for("lit-nothing"), Ee = /* @__PURE__ */ new WeakMap(), w = h.createTreeWalker(h, 129);
function De(e, t) {
	if (!_e(e) || !e.hasOwnProperty("raw")) throw Error("invalid template strings array");
	return pe === void 0 ? t : pe.createHTML(t);
}
var Oe = (e, t) => {
	let n = e.length - 1, r = [], i, a = t === 2 ? "<svg>" : t === 3 ? "<math>" : "", o = v;
	for (let t = 0; t < n; t++) {
		let n = e[t], s, c, l = -1, u = 0;
		for (; u < n.length && (o.lastIndex = u, c = o.exec(n), c !== null);) u = o.lastIndex, o === v ? c[1] === "!--" ? o = be : c[1] === void 0 ? c[2] === void 0 ? c[3] !== void 0 && (o = y) : (we.test(c[2]) && (i = RegExp("</" + c[2], "g")), o = y) : o = xe : o === y ? c[0] === ">" ? (o = i ?? v, l = -1) : c[1] === void 0 ? l = -2 : (l = o.lastIndex - c[2].length, s = c[1], o = c[3] === void 0 ? y : c[3] === "\"" ? Ce : Se) : o === Ce || o === Se ? o = y : o === be || o === xe ? o = v : (o = y, i = void 0);
		let ee = o === y && e[t + 1].startsWith("/>") ? " " : "";
		a += o === v ? n + ge : l >= 0 ? (r.push(s), n.slice(0, l) + me + n.slice(l) + m + ee) : n + m + (l === -2 ? t : ee);
	}
	return [De(e, a + (e[n] || "<?>") + (t === 2 ? "</svg>" : t === 3 ? "</math>" : "")), r];
}, ke = class e {
	constructor({ strings: t, _$litType$: n }, r) {
		let i;
		this.parts = [];
		let a = 0, o = 0, s = t.length - 1, c = this.parts, [l, u] = Oe(t, n);
		if (this.el = e.createElement(l, r), w.currentNode = this.el.content, n === 2 || n === 3) {
			let e = this.el.content.firstChild;
			e.replaceWith(...e.childNodes);
		}
		for (; (i = w.nextNode()) !== null && c.length < s;) {
			if (i.nodeType === 1) {
				if (i.hasAttributes()) for (let e of i.getAttributeNames()) if (e.endsWith(me)) {
					let t = u[o++], n = i.getAttribute(e).split(m), r = /([.?@])?(.*)/.exec(t);
					c.push({
						type: 1,
						index: a,
						name: r[2],
						strings: n,
						ctor: r[1] === "." ? Ne : r[1] === "?" ? Pe : r[1] === "@" ? Fe : Me
					}), i.removeAttribute(e);
				} else e.startsWith(m) && (c.push({
					type: 6,
					index: a
				}), i.removeAttribute(e));
				if (we.test(i.tagName)) {
					let e = i.textContent.split(m), t = e.length - 1;
					if (t > 0) {
						i.textContent = fe ? fe.emptyScript : "";
						for (let n = 0; n < t; n++) i.append(e[n], g()), w.nextNode(), c.push({
							type: 2,
							index: ++a
						});
						i.append(e[t], g());
					}
				}
			} else if (i.nodeType === 8) {
				if (i.data === he) c.push({
					type: 2,
					index: a
				});
				else {
					let e = -1;
					for (; (e = i.data.indexOf(m, e + 1)) !== -1;) c.push({
						type: 7,
						index: a
					}), e += m.length - 1;
				}
			}
			a++;
		}
	}
	static createElement(e, t) {
		let n = h.createElement("template");
		return n.innerHTML = e, n;
	}
};
function T(e, t, n = e, r) {
	if (t === S) return t;
	let i = r === void 0 ? n._$Cl : n._$Co?.[r], a = _(t) ? void 0 : t._$litDirective$;
	return i?.constructor !== a && (i?._$AO?.(!1), a === void 0 ? i = void 0 : (i = new a(e), i._$AT(e, n, r)), r === void 0 ? n._$Cl = i : (n._$Co ??= [])[r] = i), i !== void 0 && (t = T(e, i._$AS(e, t.values), i, r)), t;
}
var Ae = class {
	constructor(e, t) {
		this._$AV = [], this._$AN = void 0, this._$AD = e, this._$AM = t;
	}
	get parentNode() {
		return this._$AM.parentNode;
	}
	get _$AU() {
		return this._$AM._$AU;
	}
	u(e) {
		let { el: { content: t }, parts: n } = this._$AD, r = (e?.creationScope ?? h).importNode(t, !0);
		w.currentNode = r;
		let i = w.nextNode(), a = 0, o = 0, s = n[0];
		for (; s !== void 0;) {
			if (a === s.index) {
				let t;
				s.type === 2 ? t = new je(i, i.nextSibling, this, e) : s.type === 1 ? t = new s.ctor(i, s.name, s.strings, this, e) : s.type === 6 && (t = new Ie(i, this, e)), this._$AV.push(t), s = n[++o];
			}
			a !== s?.index && (i = w.nextNode(), a++);
		}
		return w.currentNode = h, r;
	}
	p(e) {
		let t = 0;
		for (let n of this._$AV) n !== void 0 && (n.strings === void 0 ? n._$AI(e[t]) : (n._$AI(e, n, t), t += n.strings.length - 2)), t++;
	}
}, je = class e {
	get _$AU() {
		return this._$AM?._$AU ?? this._$Cv;
	}
	constructor(e, t, n, r) {
		this.type = 2, this._$AH = C, this._$AN = void 0, this._$AA = e, this._$AB = t, this._$AM = n, this.options = r, this._$Cv = r?.isConnected ?? !0;
	}
	get parentNode() {
		let e = this._$AA.parentNode, t = this._$AM;
		return t !== void 0 && e?.nodeType === 11 && (e = t.parentNode), e;
	}
	get startNode() {
		return this._$AA;
	}
	get endNode() {
		return this._$AB;
	}
	_$AI(e, t = this) {
		e = T(this, e, t), _(e) ? e === C || e == null || e === "" ? (this._$AH !== C && this._$AR(), this._$AH = C) : e !== this._$AH && e !== S && this._(e) : e._$litType$ === void 0 ? e.nodeType === void 0 ? ve(e) ? this.k(e) : this._(e) : this.T(e) : this.$(e);
	}
	O(e) {
		return this._$AA.parentNode.insertBefore(e, this._$AB);
	}
	T(e) {
		this._$AH !== e && (this._$AR(), this._$AH = this.O(e));
	}
	_(e) {
		this._$AH !== C && _(this._$AH) ? this._$AA.nextSibling.data = e : this.T(h.createTextNode(e)), this._$AH = e;
	}
	$(e) {
		let { values: t, _$litType$: n } = e, r = typeof n == "number" ? this._$AC(e) : (n.el === void 0 && (n.el = ke.createElement(De(n.h, n.h[0]), this.options)), n);
		if (this._$AH?._$AD === r) this._$AH.p(t);
		else {
			let e = new Ae(r, this), n = e.u(this.options);
			e.p(t), this.T(n), this._$AH = e;
		}
	}
	_$AC(e) {
		let t = Ee.get(e.strings);
		return t === void 0 && Ee.set(e.strings, t = new ke(e)), t;
	}
	k(t) {
		_e(this._$AH) || (this._$AH = [], this._$AR());
		let n = this._$AH, r, i = 0;
		for (let a of t) i === n.length ? n.push(r = new e(this.O(g()), this.O(g()), this, this.options)) : r = n[i], r._$AI(a), i++;
		i < n.length && (this._$AR(r && r._$AB.nextSibling, i), n.length = i);
	}
	_$AR(e = this._$AA.nextSibling, t) {
		for (this._$AP?.(!1, !0, t); e !== this._$AB;) {
			let t = de(e).nextSibling;
			de(e).remove(), e = t;
		}
	}
	setConnected(e) {
		this._$AM === void 0 && (this._$Cv = e, this._$AP?.(e));
	}
}, Me = class {
	get tagName() {
		return this.element.tagName;
	}
	get _$AU() {
		return this._$AM._$AU;
	}
	constructor(e, t, n, r, i) {
		this.type = 1, this._$AH = C, this._$AN = void 0, this.element = e, this.name = t, this._$AM = r, this.options = i, n.length > 2 || n[0] !== "" || n[1] !== "" ? (this._$AH = Array(n.length - 1).fill(/* @__PURE__ */ new String()), this.strings = n) : this._$AH = C;
	}
	_$AI(e, t = this, n, r) {
		let i = this.strings, a = !1;
		if (i === void 0) e = T(this, e, t, 0), a = !_(e) || e !== this._$AH && e !== S, a && (this._$AH = e);
		else {
			let r = e, o, s;
			for (e = i[0], o = 0; o < i.length - 1; o++) s = T(this, r[n + o], t, o), s === S && (s = this._$AH[o]), a ||= !_(s) || s !== this._$AH[o], s === C ? e = C : e !== C && (e += (s ?? "") + i[o + 1]), this._$AH[o] = s;
		}
		a && !r && this.j(e);
	}
	j(e) {
		e === C ? this.element.removeAttribute(this.name) : this.element.setAttribute(this.name, e ?? "");
	}
}, Ne = class extends Me {
	constructor() {
		super(...arguments), this.type = 3;
	}
	j(e) {
		this.element[this.name] = e === C ? void 0 : e;
	}
}, Pe = class extends Me {
	constructor() {
		super(...arguments), this.type = 4;
	}
	j(e) {
		this.element.toggleAttribute(this.name, !!e && e !== C);
	}
}, Fe = class extends Me {
	constructor(e, t, n, r, i) {
		super(e, t, n, r, i), this.type = 5;
	}
	_$AI(e, t = this) {
		if ((e = T(this, e, t, 0) ?? C) === S) return;
		let n = this._$AH, r = e === C && n !== C || e.capture !== n.capture || e.once !== n.once || e.passive !== n.passive, i = e !== C && (n === C || r);
		r && this.element.removeEventListener(this.name, this, n), i && this.element.addEventListener(this.name, this, e), this._$AH = e;
	}
	handleEvent(e) {
		typeof this._$AH == "function" ? this._$AH.call(this.options?.host ?? this.element, e) : this._$AH.handleEvent(e);
	}
}, Ie = class {
	constructor(e, t, n) {
		this.element = e, this.type = 6, this._$AN = void 0, this._$AM = t, this.options = n;
	}
	get _$AU() {
		return this._$AM._$AU;
	}
	_$AI(e) {
		T(this, e);
	}
}, Le = ue.litHtmlPolyfillSupport;
Le?.(ke, je), (ue.litHtmlVersions ??= []).push("3.3.3");
var Re = (e, t, n) => {
	let r = n?.renderBefore ?? t, i = r._$litPart$;
	if (i === void 0) {
		let e = n?.renderBefore ?? null;
		r._$litPart$ = i = new je(t.insertBefore(g(), e), e, void 0, n ?? {});
	}
	return i._$AI(e), i;
}, ze = globalThis, E = class extends p {
	constructor() {
		super(...arguments), this.renderOptions = { host: this }, this._$Do = void 0;
	}
	createRenderRoot() {
		let e = super.createRenderRoot();
		return this.renderOptions.renderBefore ??= e.firstChild, e;
	}
	update(e) {
		let t = this.render();
		this.hasUpdated || (this.renderOptions.isConnected = this.isConnected), super.update(e), this._$Do = Re(t, this.renderRoot, this.renderOptions);
	}
	connectedCallback() {
		super.connectedCallback(), this._$Do?.setConnected(!0);
	}
	disconnectedCallback() {
		super.disconnectedCallback(), this._$Do?.setConnected(!1);
	}
	render() {
		return S;
	}
};
E._$litElement$ = !0, E.finalized = !0, ze.litElementHydrateSupport?.({ LitElement: E });
var Be = ze.litElementPolyfillSupport;
Be?.({ LitElement: E }), (ze.litElementVersions ??= []).push("4.2.2");
// src/util/queue.ts
var Ve = class extends Error {
	constructor() {
		super("superseded"), this.name = "Superseded";
	}
}, He = class {
	constructor() {
		this.order = [], this.waiting = /* @__PURE__ */ new Map(), this.busy = !1;
	}
	enqueue(e, t) {
		return new Promise((n, r) => {
			let i = {
				run: t,
				resolve: n,
				reject: r
			}, a = this.waiting.get(e);
			a ? a.push(i) : (this.waiting.set(e, [i]), this.order.push(e)), this.pump();
		});
	}
	stop(e) {
		for (let e of this.waiting.values()) for (let t of e) t.reject(new Ve());
		return this.waiting.clear(), this.order.length = 0, e();
	}
	get pending() {
		return this.order.length;
	}
	async pump() {
		if (!this.busy) {
			this.busy = !0;
			try {
				for (; this.order.length;) {
					let e = this.order.shift(), t = this.waiting.get(e);
					if (this.waiting.delete(e), !t?.length) continue;
					let n = t[t.length - 1];
					for (let e of t.slice(0, -1)) e.reject(new Ve());
					try {
						n.resolve(await n.run());
					} catch (e) {
						n.reject(e);
					}
				}
			} finally {
				this.busy = !1;
			}
		}
	}
}, D = "kohler_konnect", Ue = class {
	constructor(e) {
		this.hass = e, this.queues = /* @__PURE__ */ new Map();
	}
	subscribeDevices(e) {
		return this.hass().connection.subscribeMessage(e, { type: `${D}/subscribe_devices` });
	}
	subscribeDevice(e, t) {
		return this.hass().connection.subscribeMessage(t, {
			type: `${D}/subscribe_device`,
			device_id: e
		});
	}
	usageHistory(e, t, n, r) {
		return this.hass().callWS({
			type: `${D}/usage_history`,
			device_id: e,
			period: t,
			week_start: n,
			...r ? { anchor: r } : {}
		});
	}
	deviceDetails(e) {
		return this.hass().callWS({
			type: `${D}/device_details`,
			device_id: e
		});
	}
	restartValve(e) {
		return this.hass().callWS({
			type: `${D}/restart_valve`,
			device_id: e
		});
	}
	setWarmupAutoRestore(e, t) {
		return this.hass().callWS({
			type: `${D}/set_warmup_auto_restore`,
			device_id: e,
			enabled: t
		});
	}
	getOptions(e) {
		return this.hass().callWS({
			type: `${D}/get_options`,
			entry_id: e
		});
	}
	updateOptions(e, t) {
		return this.hass().callWS({
			type: `${D}/update_options`,
			entry_id: e,
			changes: t
		});
	}
	async signPath(e) {
		return (await this.hass().callWS({
			type: "auth/sign_path",
			path: e
		})).path;
	}
	service(e, t, n, r) {
		return this.hass().callService(e, t, n, r);
	}
	queue(e) {
		let t = this.queues.get(e);
		return t || (t = new He(), this.queues.set(e, t)), t;
	}
}, We = {
	account: "M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M7.07,18.28C7.5,17.38 10.12,16.5 12,16.5C13.88,16.5 16.5,17.38 16.93,18.28C15.57,19.36 13.86,20 12,20C10.14,20 8.43,19.36 7.07,18.28M18.36,16.83C16.93,15.09 13.46,14.5 12,14.5C10.54,14.5 7.07,15.09 5.64,16.83C4.62,15.5 4,13.82 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,13.82 19.38,15.5 18.36,16.83M12,6C10.06,6 8.5,7.56 8.5,9.5C8.5,11.44 10.06,13 12,13C13.94,13 15.5,11.44 15.5,9.5C15.5,7.56 13.94,6 12,6M12,11A1.5,1.5 0 0,1 10.5,9.5A1.5,1.5 0 0,1 12,8A1.5,1.5 0 0,1 13.5,9.5A1.5,1.5 0 0,1 12,11Z",
	alert: "M11,15H13V17H11V15M11,7H13V13H11V7M12,2C6.47,2 2,6.5 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M12,20A8,8 0 0,1 4,12A8,8 0 0,1 12,4A8,8 0 0,1 20,12A8,8 0 0,1 12,20Z",
	back: "M15.41,16.58L10.83,12L15.41,7.41L14,6L8,12L14,18L15.41,16.58Z",
	check: "M21,7L9,19L3.5,13.5L4.91,12.09L9,16.17L19.59,5.59L21,7Z",
	checkCircle: "M12 2C6.5 2 2 6.5 2 12S6.5 22 12 22 22 17.5 22 12 17.5 2 12 2M10 17L5 12L6.41 10.59L10 14.17L17.59 6.58L19 8L10 17Z",
	chevronDown: "M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z",
	chevronRight: "M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z",
	close: "M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z",
	cloud: "M6.5 20Q4.22 20 2.61 18.43 1 16.85 1 14.58 1 12.63 2.17 11.1 3.35 9.57 5.25 9.15 5.88 6.85 7.75 5.43 9.63 4 12 4 14.93 4 16.96 6.04 19 8.07 19 11 20.73 11.2 21.86 12.5 23 13.78 23 15.5 23 17.38 21.69 18.69 20.38 20 18.5 20M6.5 18H18.5Q19.55 18 20.27 17.27 21 16.55 21 15.5 21 14.45 20.27 13.73 19.55 13 18.5 13H17V11Q17 8.93 15.54 7.46 14.08 6 12 6 9.93 6 8.46 7.46 7 8.93 7 11H6.5Q5.05 11 4.03 12.03 3 13.05 3 14.5 3 15.95 4.03 17 5.05 18 6.5 18M12 12Z",
	controls: "M8 13C6.14 13 4.59 14.28 4.14 16H2V18H4.14C4.59 19.72 6.14 21 8 21S11.41 19.72 11.86 18H22V16H11.86C11.41 14.28 9.86 13 8 13M8 19C6.9 19 6 18.1 6 17C6 15.9 6.9 15 8 15S10 15.9 10 17C10 18.1 9.1 19 8 19M19.86 6C19.41 4.28 17.86 3 16 3S12.59 4.28 12.14 6H2V8H12.14C12.59 9.72 14.14 11 16 11S19.41 9.72 19.86 8H22V6H19.86M16 9C14.9 9 14 8.1 14 7C14 5.9 14.9 5 16 5S18 5.9 18 7C18 8.1 17.1 9 16 9Z",
	cup: "M18.32,8H5.67L5.23,4H18.77M12,19A3,3 0 0,1 9,16C9,14 12,10.6 12,10.6C12,10.6 15,14 15,16A3,3 0 0,1 12,19M3,2L5,20.23C5.13,21.23 5.97,22 7,22H17C18,22 18.87,21.23 19,20.23L21,2H3Z",
	download: "M5,20H19V18H5M19,9H15V3H9V9H5L12,16L19,9Z",
	experiences: "M9 4L11.5 9.5L17 12L11.5 14.5L9 20L6.5 14.5L1 12L6.5 9.5L9 4M9 8.83L8 11L5.83 12L8 13L9 15.17L10 13L12.17 12L10 11L9 8.83M19 9L17.74 6.26L15 5L17.74 3.75L19 1L20.25 3.75L23 5L20.25 6.26L19 9M19 23L17.74 20.26L15 19L17.74 17.75L19 15L20.25 17.75L23 19L20.25 20.26L19 23Z",
	fan: "M12,11A1,1 0 0,0 11,12A1,1 0 0,0 12,13A1,1 0 0,0 13,12A1,1 0 0,0 12,11M12.5,2C17,2 17.11,5.57 14.75,6.75C13.76,7.24 13.32,8.29 13.13,9.22C13.61,9.42 14.03,9.73 14.35,10.13C18.05,8.13 22.03,8.92 22.03,12.5C22.03,17 18.46,17.1 17.28,14.73C16.78,13.74 15.72,13.3 14.79,13.11C14.59,13.59 14.28,14 13.88,14.34C15.87,18.03 15.08,22 11.5,22C7,22 6.91,18.42 9.27,17.24C10.25,16.75 10.69,15.71 10.89,14.79C10.4,14.59 9.97,14.27 9.65,13.87C5.96,15.85 2,15.07 2,11.5C2,7 5.56,6.89 6.74,9.26C7.24,10.25 8.29,10.68 9.22,10.87C9.41,10.39 9.73,9.97 10.14,9.65C8.15,5.96 8.94,2 12.5,2Z",
	faucet: "M19 14V16H16V14.28L19 14M19 13C19 11.9 18 11 16.8 11H10V10H5V21H10V13.91L19 13M5 9H10V7L15.36 5.21C15.74 5.09 16 4.73 16 4.33C16 3.68 15.36 3.23 14.75 3.45L5 7V9Z",
	favorite: "M12.1,18.55L12,18.65L11.89,18.55C7.14,14.24 4,11.39 4,8.5C4,6.5 5.5,5 7.5,5C9.04,5 10.54,6 11.07,7.36H12.93C13.46,6 14.96,5 16.5,5C18.5,5 20,6.5 20,8.5C20,11.39 16.86,14.24 12.1,18.55M16.5,3C14.76,3 13.09,3.81 12,5.08C10.91,3.81 9.24,3 7.5,3C4.42,3 2,5.41 2,8.5C2,12.27 5.4,15.36 10.55,20.03L12,21.35L13.45,20.03C18.6,15.36 22,12.27 22,8.5C22,5.41 19.58,3 16.5,3Z",
	favoriteFilled: "M12,21.35L10.55,20.03C5.4,15.36 2,12.27 2,8.5C2,5.41 4.42,3 7.5,3C9.24,3 10.91,3.81 12,5.08C13.09,3.81 14.76,3 16.5,3C19.58,3 22,5.41 22,8.5C22,12.27 18.6,15.36 13.45,20.03L12,21.35Z",
	help: "M11,18H13V16H11V18M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,6A4,4 0 0,0 8,10H10A2,2 0 0,1 12,8A2,2 0 0,1 14,10C14,12 11,11.75 11,15H13C13,12.75 16,12.5 16,10A4,4 0 0,0 12,6Z",
	info: "M11,9H13V7H11M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M11,17H13V11H11V17Z",
	light: "M12,2A7,7 0 0,1 19,9C19,11.38 17.81,13.47 16,14.74V17A1,1 0 0,1 15,18H9A1,1 0 0,1 8,17V14.74C6.19,13.47 5,11.38 5,9A7,7 0 0,1 12,2M9,21V20H15V21A1,1 0 0,1 14,22H10A1,1 0 0,1 9,21M12,4A5,5 0 0,0 7,9C7,11.05 8.23,12.81 10,13.58V16H14V13.58C15.77,12.81 17,11.05 17,9A5,5 0 0,0 12,4Z",
	lock: "M12,17A2,2 0 0,0 14,15C14,13.89 13.1,13 12,13A2,2 0 0,0 10,15A2,2 0 0,0 12,17M18,8A2,2 0 0,1 20,10V20A2,2 0 0,1 18,22H6A2,2 0 0,1 4,20V10C4,8.89 4.9,8 6,8H7V6A5,5 0 0,1 12,1A5,5 0 0,1 17,6V8H18M12,3A3,3 0 0,0 9,6V8H15V6A3,3 0 0,0 12,3Z",
	menu: "M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z",
	minus: "M19,13H5V11H19V13Z",
	more: "M16,12A2,2 0 0,1 18,10A2,2 0 0,1 20,12A2,2 0 0,1 18,14A2,2 0 0,1 16,12M10,12A2,2 0 0,1 12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12M4,12A2,2 0 0,1 6,10A2,2 0 0,1 8,12A2,2 0 0,1 6,14A2,2 0 0,1 4,12Z",
	music: "M12 3V13.55C11.41 13.21 10.73 13 10 13C7.79 13 6 14.79 6 17S7.79 21 10 21 14 19.21 14 17V7H18V3H12Z",
	night: "M17.75,4.09L15.22,6.03L16.13,9.09L13.5,7.28L10.87,9.09L11.78,6.03L9.25,4.09L12.44,4L13.5,1L14.56,4L17.75,4.09M21.25,11L19.61,12.25L20.2,14.23L18.5,13.06L16.8,14.23L17.39,12.25L15.75,11L17.81,10.95L18.5,9L19.19,10.95L21.25,11M18.97,15.95C19.8,15.87 20.69,17.05 20.16,17.8C19.84,18.25 19.5,18.67 19.08,19.07C15.17,23 8.84,23 4.94,19.07C1.03,15.17 1.03,8.83 4.94,4.93C5.34,4.53 5.76,4.17 6.21,3.85C6.96,3.32 8.14,4.21 8.06,5.04C7.79,7.9 8.75,10.87 10.95,13.06C13.14,15.26 16.1,16.22 18.97,15.95M17.33,17.97C14.5,17.81 11.7,16.64 9.53,14.5C7.36,12.31 6.2,9.5 6.04,6.68C3.23,9.82 3.34,14.64 6.35,17.66C9.37,20.67 14.19,20.78 17.33,17.97Z",
	pause: "M14,19H18V5H14M6,19H10V5H6V19Z",
	play: "M8,5.14V19.14L19,12.14L8,5.14Z",
	plus: "M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z",
	restart: "M12,4C14.1,4 16.1,4.8 17.6,6.3C20.7,9.4 20.7,14.5 17.6,17.6C15.8,19.5 13.3,20.2 10.9,19.9L11.4,17.9C13.1,18.1 14.9,17.5 16.2,16.2C18.5,13.9 18.5,10.1 16.2,7.7C15.1,6.6 13.5,6 12,6V10.6L7,5.6L12,0.6V4M6.3,17.6C3.7,15 3.3,11 5.1,7.9L6.6,9.4C5.5,11.6 5.9,14.4 7.8,16.2C8.3,16.7 8.9,17.1 9.6,17.4L9,19.4C8,19 7.1,18.4 6.3,17.6Z",
	settings: "M12,8A4,4 0 0,1 16,12A4,4 0 0,1 12,16A4,4 0 0,1 8,12A4,4 0 0,1 12,8M12,10A2,2 0 0,0 10,12A2,2 0 0,0 12,14A2,2 0 0,0 14,12A2,2 0 0,0 12,10M10,22C9.75,22 9.54,21.82 9.5,21.58L9.13,18.93C8.5,18.68 7.96,18.34 7.44,17.94L4.95,18.95C4.73,19.03 4.46,18.95 4.34,18.73L2.34,15.27C2.21,15.05 2.27,14.78 2.46,14.63L4.57,12.97L4.5,12L4.57,11L2.46,9.37C2.27,9.22 2.21,8.95 2.34,8.73L4.34,5.27C4.46,5.05 4.73,4.96 4.95,5.05L7.44,6.05C7.96,5.66 8.5,5.32 9.13,5.07L9.5,2.42C9.54,2.18 9.75,2 10,2H14C14.25,2 14.46,2.18 14.5,2.42L14.87,5.07C15.5,5.32 16.04,5.66 16.56,6.05L19.05,5.05C19.27,4.96 19.54,5.05 19.66,5.27L21.66,8.73C21.79,8.95 21.73,9.22 21.54,9.37L19.43,11L19.5,12L19.43,13L21.54,14.63C21.73,14.78 21.79,15.05 21.66,15.27L19.66,18.73C19.54,18.95 19.27,19.04 19.05,18.95L16.56,17.95C16.04,18.34 15.5,18.68 14.87,18.93L14.5,21.58C14.46,21.82 14.25,22 14,22H10M11.25,4L10.88,6.61C9.68,6.86 8.62,7.5 7.85,8.39L5.44,7.35L4.69,8.65L6.8,10.2C6.4,11.37 6.4,12.64 6.8,13.8L4.68,15.36L5.43,16.66L7.86,15.62C8.63,16.5 9.68,17.14 10.87,17.38L11.24,20H12.76L13.13,17.39C14.32,17.14 15.37,16.5 16.14,15.62L18.57,16.66L19.32,15.36L17.2,13.81C17.6,12.64 17.6,11.37 17.2,10.2L19.31,8.65L18.56,7.35L16.15,8.39C15.38,7.5 14.32,6.86 13.12,6.62L12.75,4H11.25Z",
	stop: "M18,18H6V6H18V18Z",
	thermometer: "M15 13V5A3 3 0 0 0 9 5V13A5 5 0 1 0 15 13M12 4A1 1 0 0 1 13 5V8H11V5A1 1 0 0 1 12 4Z",
	timer: "M12,20A7,7 0 0,1 5,13A7,7 0 0,1 12,6A7,7 0 0,1 19,13A7,7 0 0,1 12,20M19.03,7.39L20.45,5.97C20,5.46 19.55,5 19.04,4.56L17.62,6C16.07,4.74 14.12,4 12,4A9,9 0 0,0 3,13A9,9 0 0,0 12,22C17,22 21,17.97 21,13C21,10.88 20.26,8.93 19.03,7.39M11,14H13V8H11M15,1H9V3H15V1Z",
	water: "M12,20A6,6 0 0,1 6,14C6,10 12,3.25 12,3.25C12,3.25 18,10 18,14A6,6 0 0,1 12,20Z",
	waterOutline: "M12,3.77L11.25,4.61C11.25,4.61 9.97,6.06 8.68,7.94C7.39,9.82 6,12.07 6,14.23A6,6 0 0,0 12,20.23A6,6 0 0,0 18,14.23C18,12.07 16.61,9.82 15.32,7.94C14.03,6.06 12.75,4.61 12.75,4.61L12,3.77M12,6.9C12.44,7.42 12.84,7.85 13.68,9.07C14.89,10.83 16,13.07 16,14.23C16,16.45 14.22,18.23 12,18.23C9.78,18.23 8,16.45 8,14.23C8,13.07 9.11,10.83 10.32,9.07C11.16,7.85 11.56,7.42 12,6.9Z",
	wifi: "M12,21L15.6,16.2C14.6,15.45 13.35,15 12,15C10.65,15 9.4,15.45 8.4,16.2L12,21M12,3C7.95,3 4.21,4.34 1.2,6.6L3,9C5.5,7.12 8.62,6 12,6C15.38,6 18.5,7.12 21,9L22.8,6.6C19.79,4.34 16.05,3 12,3M12,9C9.3,9 6.81,9.89 4.8,11.4L6.6,13.8C8.1,12.67 9.97,12 12,12C14.03,12 15.9,12.67 17.4,13.8L19.2,11.4C17.19,9.89 14.7,9 12,9Z"
};
function O(e, t) {
	return x`<svg viewBox="0 0 24 24" aria-hidden=${t ? "false" : "true"} role=${t ? "img" : "presentation"}>${t ? x`<title>${t}</title>` : null}<path fill="currentColor" d=${We[e]}></path></svg>`;
}
function Ge(e, t = "") {
	if (e !== null) {
		if (e === 1) return "handshower";
		if (e === 11) return "showerhead";
		if (e === 12) return "showerheads";
		if (e === 21) return "tubfiller";
		if (e === 30) return "notplumbed";
		if (e >= 31 && e <= 39) return "rainhead";
		if (e >= 51 && e <= 53) return "bodyspray";
		if (e >= 61 && e <= 62) return "footspray";
	}
	let n = t.toLowerCase();
	return n.includes("rain") ? "rainhead" : n.includes("hand") ? "handshower" : n.includes("body") ? "bodyspray" : n.includes("foot") ? "footspray" : n.includes("tub") || n.includes("bath") ? "tubfiller" : n.includes("shower") ? "showerhead" : "outlet";
}
var k = "1.6 4.4", Ke = {
	rainhead: x`
    <line x1="24" y1="4" x2="24" y2="11" />
    <rect x="7" y="11" width="34" height="5" rx="2.5" />
    <g class="spray" stroke-dasharray=${k}>
      <line x1="11" y1="21" x2="11" y2="44" />
      <line x1="16.2" y1="21" x2="16.2" y2="44" />
      <line x1="21.4" y1="21" x2="21.4" y2="44" />
      <line x1="26.6" y1="21" x2="26.6" y2="44" />
      <line x1="31.8" y1="21" x2="31.8" y2="44" />
      <line x1="37" y1="21" x2="37" y2="44" />
    </g>`,
	showerhead: x`
    <line x1="7" y1="4" x2="7" y2="12" />
    <path d="M7 8 H29 V11" />
    <path d="M25 11 H33 L39 21 H19 Z" />
    <g class="spray" stroke-dasharray=${k}>
      <line x1="21" y1="25" x2="17" y2="44" />
      <line x1="25" y1="25" x2="23" y2="44" />
      <line x1="29" y1="25" x2="29" y2="44" />
      <line x1="33" y1="25" x2="35" y2="44" />
      <line x1="37" y1="25" x2="41" y2="44" />
    </g>`,
	showerheads: x`
    <line x1="6" y1="6" x2="6" y2="10" />
    <path d="M3 10 H9 L12 16 H0 Z" transform="translate(4 0)" />
    <line x1="34" y1="6" x2="34" y2="10" />
    <path d="M31 10 H37 L40 16 H28 Z" transform="translate(4 0)" />
    <g class="spray" stroke-dasharray=${k}>
      <line x1="7" y1="20" x2="5" y2="42" />
      <line x1="10" y1="20" x2="10" y2="42" />
      <line x1="13" y1="20" x2="15" y2="42" />
      <line x1="35" y1="20" x2="33" y2="42" />
      <line x1="38" y1="20" x2="38" y2="42" />
      <line x1="41" y1="20" x2="43" y2="42" />
    </g>`,
	handshower: x`
    <circle cx="16" cy="14" r="6.5" />
    <line x1="20.5" y1="18.5" x2="31" y2="35" />
    <path d="M31 35 C34 41 38 43.5 44 43.5" />
    <g class="spray" stroke-dasharray=${k}>
      <line x1="11" y1="21" x2="5" y2="34" />
      <line x1="15" y1="22" x2="11" y2="38" />
      <line x1="19" y1="23" x2="17" y2="42" />
    </g>`,
	bodyspray: x`
    <rect x="5" y="8" width="8" height="32" rx="4" />
    <g class="spray" stroke-dasharray=${k}>
      <line x1="18" y1="16" x2="44" y2="16" />
      <line x1="18" y1="24" x2="44" y2="24" />
      <line x1="18" y1="32" x2="44" y2="32" />
    </g>`,
	footspray: x`
    <rect x="5" y="28" width="8" height="14" rx="4" />
    <g class="spray" stroke-dasharray=${k}>
      <line x1="18" y1="31" x2="44" y2="34" />
      <line x1="18" y1="38" x2="44" y2="43" />
    </g>
    <line x1="5" y1="45" x2="44" y2="45" opacity="0.5" />`,
	tubfiller: x`
    <path d="M5 12 H26 A6 6 0 0 1 32 18 V22" />
    <g class="spray" stroke-dasharray=${k}>
      <line x1="32" y1="26" x2="32" y2="40" />
    </g>
    <path d="M12 40 H44 V42 A4 4 0 0 1 40 46 H16 A4 4 0 0 1 12 42 Z" />`,
	notplumbed: x`
    <circle cx="24" cy="24" r="15" stroke-dasharray="3 4" />
    <line x1="14" y1="34" x2="34" y2="14" />`,
	outlet: x`
    <path d="M24 6 C24 6 12 20 12 29 A12 12 0 0 0 36 29 C36 20 24 6 24 6 Z" />`
};
function qe(e) {
	return x`<svg class="fixture" viewBox="0 0 48 48" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${Ke[e]}</svg>`;
}
// src/theme/shared.ts
var A = o`
  :host {
    font-family: var(--kk-font);
    color: var(--kk-text);
    -webkit-font-smoothing: antialiased;
    -webkit-tap-highlight-color: transparent;
  }

  * {
    box-sizing: border-box;
  }

  button {
    font: inherit;
    color: inherit;
    border: none;
    background: none;
    padding: 0;
    cursor: pointer;
  }

  button:disabled {
    cursor: default;
  }

  :focus-visible {
    outline: 2px solid var(--kk-accent);
    outline-offset: 3px;
  }

  /* A control reached by keyboard scrolls clear of the sticky header and the bars along
     the bottom (the capsule, the shower's Start). */
  button,
  a,
  input,
  [tabindex] {
    scroll-margin-block: 88px 170px;
  }

  .round {
    display: inline-grid;
    place-items: center;
    width: 48px;
    height: 48px;
    border-radius: 50%;
    background: var(--kk-control);
    color: var(--kk-text);
    transition: background 160ms var(--kk-ease), transform 160ms var(--kk-ease);
    flex: none;
  }

  .round:hover:not(:disabled):not([aria-disabled="true"]) {
    background: var(--kk-control-hover);
  }

  .round:active:not(:disabled) {
    transform: scale(0.94);
  }

  .round:disabled,
  .round[aria-disabled="true"] {
    opacity: 0.4;
  }

  .round svg {
    width: 24px;
    height: 24px;
  }

  .pill {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 44px;
    padding: 0 28px;
    border-radius: var(--kk-radius-pill);
    border: 1.5px solid var(--kk-text-2);
    font-size: 17px;
    letter-spacing: 0.01em;
    transition: background 160ms var(--kk-ease), border-color 160ms var(--kk-ease),
      transform 160ms var(--kk-ease), opacity 160ms;
  }

  .pill svg {
    width: 22px;
    height: 22px;
    flex: none;
  }

  .pill:hover:not(:disabled) {
    border-color: var(--kk-text);
  }

  .pill:active:not(:disabled) {
    transform: scale(0.97);
  }

  .pill:disabled,
  .pill[aria-disabled="true"] {
    opacity: 0.4;
  }

  .pill.filled {
    border-color: transparent;
    background: var(--kk-accent-fill);
    color: var(--kk-accent-on);
  }

  .pill.filled:hover:not(:disabled) {
    border-color: transparent;
    filter: brightness(1.08);
  }

  .pill.danger {
    border-color: transparent;
    background: var(--kk-danger-fill);
    color: #fff;
  }

  .card {
    background: var(--kk-surface);
    border-radius: var(--kk-radius-card);
    box-shadow: var(--kk-shadow);
  }

  .section-label {
    font-size: 17px;
    font-weight: 400;
    color: var(--kk-text-2);
    margin: 0;
  }

  .eyebrow {
    font-size: 13px;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--kk-text-3);
  }

  .muted {
    color: var(--kk-text-2);
  }

  .accent {
    color: var(--kk-accent-text);
  }

  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    border: 0;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }

  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      animation-duration: 1ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 1ms !important;
    }
  }
`;
// src/components/define.ts
function j(e, t) {
	customElements.get(e) || customElements.define(e, t);
}
j("kkp-capsule", class extends E {
	static {
		this.properties = {
			items: { attribute: !1 },
			active: {}
		};
	}
	static {
		this.styles = [A, o`
      :host {
        display: flex;
        justify-content: center;
        position: sticky;
        bottom: 0;
        padding: 20px 16px max(20px, env(safe-area-inset-bottom));
        background: linear-gradient(to top, var(--kk-canvas) 55%, transparent);
        pointer-events: none;
        z-index: 2;
      }

      nav {
        display: flex;
        gap: 4px;
        padding: 6px;
        border-radius: var(--kk-radius-pill);
        background: var(--kk-nav);
        box-shadow: var(--kk-shadow);
        pointer-events: auto;
      }

      button {
        display: grid;
        justify-items: center;
        gap: 4px;
        min-width: 104px;
        padding: 10px 18px 8px;
        border-radius: var(--kk-radius-pill);
        color: var(--kk-text);
        font-size: 14px;
        transition: background 200ms var(--kk-ease), color 200ms var(--kk-ease);
      }

      button svg {
        width: 26px;
        height: 26px;
      }

      button[aria-current="page"] {
        background: var(--kk-nav-active);
        color: var(--kk-accent);
        font-weight: 500;
      }

      /* Three items still fit a 320 px screen. */
      @media (max-width: 359px) {
        button {
          min-width: 0;
          padding: 10px 12px 8px;
        }
      }
    `];
	}
	render() {
		return b`<nav>
      ${this.items.map((e) => b`<button
          aria-current=${e.key === this.active ? "page" : "false"}
          @click=${() => this.dispatchEvent(new CustomEvent("kkp-select", { detail: e.key }))}
        >
          ${O(e.icon)}<span>${e.label}</span>
        </button>`)}
    </nav>`;
	}
});
// src/i18n/de.json
var Je = {
	"app.title": "Kohler Konnect",
	"app.loading": "Verbindung zu Kohler Konnect wird hergestellt …",
	"app.no_devices": "Noch keine Kohler-Geräte",
	"app.no_devices_body": "Geräte in deinem Kohler-Konnect-Konto erscheinen hier, sobald die Integration sie ausgelesen hat.",
	"app.stale_bundle": "Das Panel wurde aktualisiert. Aktualisiere die Seite, um die neue Version zu laden.",
	"app.refresh": "Aktualisieren",
	"app.not_found": "Dieses Gerät ist nicht mehr im Konto.",
	"nav.back": "Zurück",
	"nav.more": "Mehr",
	"nav.menu": "Menü",
	"nav.controls": "Steuerung",
	"nav.favorites": "Favoriten",
	"nav.experiences": "Erlebnisse",
	"nav.devices": "Geräte",
	"nav.usage": "Verbrauch",
	"nav.account": "Konto",
	"home.welcome": "Willkommen, {name}",
	"home.welcome_anonymous": "Willkommen",
	"home.help": "Brauchst du Hilfe?",
	"home.summary_quiet": "Alles ruhig",
	"home.summary_running": "Aktiv: {count}",
	"home.summary_alert": "Bitte prüfen: {count}",
	"home.other_area": "Sonstige",
	"home.usage_this_month": "Dieser Monat",
	"home.usage_today": "Heute",
	"home.usage_last_7_days": "Letzte 7 Tage",
	"status.idle": "Bereit",
	"status.ready": "Bereit",
	"status.off": "Aus",
	"status.on": "An",
	"status.offline": "Offline",
	"status.running": "Läuft",
	"status.warming": "Vorwärmen",
	"status.paused": "Pausiert",
	"status.water_on": "Wasser an",
	"status.dispensing": "Dosierung",
	"status.leak": "Leck erkannt",
	"status.problem": "Bitte prüfen",
	"status.minutes_left": "Noch {time}",
	"status.untested": "Ungetestet",
	"shower.simple_start": "Schnellstart",
	"shower.simple_start_unknown": "Die Armatur hat ihre Standarddusche noch nicht gemeldet",
	"shower.outlets": "Auslässe",
	"shower.zone": "Zone {zone}",
	"shower.temperature": "Temperatur",
	"shower.flow": "Durchfluss",
	"shower.start": "Start",
	"shower.stop": "Stopp",
	"shower.resume": "Fortsetzen",
	"shower.cold": "Kalt",
	"shower.staged_hint": "Wähle Auslässe aus und drücke dann auf Start",
	"shower.locked_warming": "Änderungen sind gesperrt, während die Armatur vorwärmt",
	"shower.locked": "Gesperrt",
	"shower.set_to": "Eingestellt auf {temperature}",
	"shower.at_temperature": "Temperatur erreicht",
	"shower.heating": "Heizt auf",
	"shower.warming_title": "Vorwärmen",
	"shower.running_title": "Wasser läuft",
	"shower.paused_title": "Bereit, wenn du es bist",
	"shower.paused_body": "Vorwärmen abgeschlossen. Mit Fortsetzen startest du deine Dusche.",
	"shower.time_left": "Restzeit",
	"scenes.empty_favorites": "Noch keine Favoriten",
	"scenes.empty_favorites_body": "Favoriten, die du in der Konnect-App oder am Touchscreen erstellst, erscheinen hier.",
	"scenes.empty_experiences": "Noch keine Erlebnisse",
	"scenes.empty_experiences_body": "Erlebnisse, die du in der Konnect-App hinzufügst, erscheinen hier.",
	"scenes.start": "Start",
	"scenes.running": "Läuft",
	"controller.steam": "Dampf",
	"controller.steam_start": "Dampf starten",
	"controller.steam_stop": "Dampf stoppen",
	"controller.steam_blocked": "Dampf ist nicht möglich, solange die Dusche läuft",
	"controller.status": "Status",
	"controller.music": "Musik",
	"controller.light": "Licht",
	"controller.read_only": "Nur Anzeige: Der Controller startet Favoriten, keine einzelnen Auslässe.",
	"faucet.faucet": "Wasserhahn",
	"faucet.dispense_amount": "Dosiermenge",
	"faucet.start": "Start",
	"faucet.stop": "Stopp",
	"faucet.see_more": "Mehr anzeigen",
	"faucet.see_less": "Weniger anzeigen",
	"faucet.presets": "Voreinstellungen",
	"faucet.no_presets": "Voreinstellungen, die du in der Konnect-App erstellst, erscheinen hier.",
	"faucet.custom": "Eigene Menge",
	"faucet.auto_off": "Schaltet sich in {time} automatisch ab",
	"faucet.handle_closed": "Öffne den Hebel, um das Wasser aus der Ferne laufen zu lassen.",
	"faucet.firmware_downloading": "Die Armatur lädt Firmware herunter. Wasser lässt sich erst danach einschalten.",
	"faucet.leak": "Kohler hat ein Leck an dieser Armatur gemeldet.",
	"faucet.clear_leak": "Zurücksetzen",
	"faucet.last_dispensed": "Zuletzt {amount} dosiert",
	"settings.title": "Einstellungen",
	"settings.locked_running": "Einstellungen lassen sich nicht ändern, während Wasser läuft.",
	"settings.defaults": "Standardeinstellungen",
	"settings.defaults_body": "Diese Werte gelten standardmäßig, wenn du deine Dusche startest.",
	"settings.default_temperature": "Temperatur",
	"settings.default_outlets": "Auslässe",
	"settings.default_outlets_body": "Die Standard-Auslässe änderst du in der Konnect-App.",
	"settings.device_control": "Gerätesteuerung",
	"settings.warmup": "Dusche vorwärmen",
	"settings.warmup_body": "Wärmt das Wasser vor dem Duschen vor und wartet dann, bis du bereit bist.",
	"settings.warmup_outlets": "Auslässe zum Vorwärmen",
	"settings.warmup_auto_restore": "Vorwärmen wiederherstellen",
	"settings.warmup_auto_restore_body": "Schaltet das Vorwärmen wieder ein, wenn etwas es unbemerkt ausschaltet.",
	"settings.max_temperature": "Höchsttemperatur",
	"settings.max_duration": "Maximale Duschdauer",
	"settings.flow": "Durchfluss",
	"settings.general": "Allgemein",
	"settings.about": "Info",
	"settings.water_usage": "Wasserverbrauch",
	"settings.diagnostics": "Diagnose",
	"settings.restart": "Produkt neu starten",
	"settings.save": "Speichern",
	"settings.cancel": "Abbrechen",
	"settings.dispense_defaults": "Dosierung",
	"settings.dispense_amount": "Dosiermenge",
	"settings.preset": "Voreinstellung",
	"settings.safety_limit": "Wasser-Sicherheitslimit",
	"settings.safety_limit_body": "Gilt für das ganze Konto und wird unter Konto eingestellt.",
	"settings.web_settings": "Webeinstellungen des Controllers",
	"settings.scald_title": "Verbrühschutz ändern?",
	"settings.scald_body": "Die Höchsttemperatur legt fest, wie heiß das Wasser dieser Dusche werden kann. Erhöhst du sie auf {temperature}, kann jeder Auslass so heißes Wasser liefern.",
	"settings.scald_confirm": "Grenze ändern",
	"settings.restart_title": "{name} neu starten?",
	"settings.restart_body": "Die Armatur startet neu, wie bei „Produkt neu starten“ in der Konnect-App. Laufendes Wasser stoppt.",
	"settings.restart_confirm": "Neu starten",
	"settings.restart_done": "Neustart gesendet.",
	"settings.touchscreen": "Bedienfeld",
	"settings.touchscreen_body": "Jede Änderung wird wie im Bedienfeld der Konnect-App auf alle Bedienfelder zugleich geschrieben.",
	"settings.panel_language": "Panelsprache",
	"settings.panel_units": "Paneleinheiten",
	"settings.wake_screen": "Startbildschirm",
	"settings.display_brightness": "Displayhelligkeit",
	"settings.haptic_feedback": "Haptisches Feedback",
	"settings.accessible_display": "Barrierefreie Anzeige",
	"settings.accessible_display_body": "Fügt Bildschirmtasten hinzu, die den Drehregler nachbilden. Die Bedienfelder brauchen einen Moment, um sich zu aktualisieren.",
	"settings.flow_control": "Durchflusssteuerung",
	"settings.flow_control_body": "Ermöglicht, den Durchfluss auf dem Hauptbildschirm des Bedienfelds einzustellen.",
	"settings.water_saving_mode": "Wassersparmodus",
	"settings.water_saving_mode_body": "Bei der Installation festgelegt: immer nur ein Auslass. Auch die Konnect-App ändert ihn nicht.",
	"about.title": "Info",
	"about.model": "Modell",
	"about.serial": "Seriennummer",
	"about.install_date": "Installationsdatum",
	"about.registered": "Registriert",
	"about.hardware": "Hardware",
	"about.firmware": "Firmware",
	"about.firmware.interface": "Bedienfeld",
	"about.firmware.valve": "Armatur",
	"about.firmware.second_valve": "Zweite Armatur",
	"about.firmware.gateway": "Gateway",
	"about.firmware.controller": "Controller",
	"about.firmware.application": "Anwendung",
	"about.firmware.bootloader": "Bootloader",
	"about.firmware.os": "Betriebssystem",
	"about.firmware.certificate": "Zertifikat",
	"about.firmware.service_pack": "Service Pack",
	"about.up_to_date": "Aktuell",
	"about.update_available": "Update verfügbar: {version}",
	"about.update_in_app": "Installiere Updates über die Konnect-App.",
	"about.technical": "Technische Informationen",
	"about.gateway": "Gateway",
	"about.valves": "Armaturen",
	"about.interfaces": "Bedienfelder",
	"about.status": "Status",
	"about.leak_history": "Leckverlauf",
	"about.no_leaks": "Keine Lecks gemeldet",
	"about.unknown": "Nicht gemeldet",
	"usage.title": "Wasserverbrauch",
	"usage.week": "Woche",
	"usage.month": "Monat",
	"usage.year": "Jahr",
	"usage.average": "Durchschnitt",
	"usage.high": "Höchstwert",
	"usage.total": "Gesamt",
	"usage.water": "Wasser",
	"usage.time": "Laufzeit",
	"usage.per_day": "pro Tag",
	"usage.previous": "Vorheriger Zeitraum",
	"usage.next": "Nächster Zeitraum",
	"usage.calendar_week": "Kalenderwoche. Der Sensor „diese Woche“ umfasst immer die letzten sieben Tage.",
	"usage.error": "Kohler hat für diesen Zeitraum keinen Verbrauch geliefert.",
	"usage.retry": "Erneut versuchen",
	"usage.equivalent_bath": "Etwa {count}-mal eine volle Badewanne",
	"diagnostics.title": "Diagnose",
	"diagnostics.stream": "Kohler-Verbindung",
	"diagnostics.stream_body": "Die Live-Verbindung von Home Assistant zur Kohler-Cloud.",
	"diagnostics.cloud": "Gerät online",
	"diagnostics.cloud_body": "Ob die Kohler-Cloud dieses Gerät erreichen kann.",
	"diagnostics.connected": "Verbunden",
	"diagnostics.disconnected": "Getrennt",
	"diagnostics.unknown": "Noch nicht geprüft",
	"diagnostics.last_update": "Letzte Aktualisierung",
	"diagnostics.faults": "Aktive Fehler",
	"diagnostics.no_faults": "Keine aktiven Fehler",
	"diagnostics.clear_errors": "Fehlerprotokoll löschen",
	"diagnostics.report_log": "Report Log",
	"diagnostics.report_log_body": "Zeichnet jede Kohler-Nachricht für einen Fehlerbericht auf.",
	"diagnostics.download": "Diagnosedaten herunterladen",
	"diagnostics.download_body": "Ein Bericht über die gesamte Installation, mit geschwärzten Zugangsdaten und Seriennummern.",
	"account.admin_only": "Kontoeinstellungen sind nur für Administratoren verfügbar.",
	"account.connection": "Kohler-Verbindung",
	"account.zone_grouping": "Zonengruppierung",
	"account.zone_grouping.numbered": "Zonennummern",
	"account.zone_grouping.subdevices": "Eigenes Gerät pro Zone",
	"account.zone_grouping.outlet_labels": "Auslassnamen an den Steuerungen",
	"account.safety_limit": "Wasser-Sicherheitslimit",
	"account.safety_limit_body": "Schaltet Wasser, das Home Assistant an einer Armatur eingeschaltet hat, nach dieser Zeit wieder ab. 0 deaktiviert das Limit.",
	"account.minutes": "{count} min",
	"account.off": "Aus",
	"account.sidebar_panel": "Dieses Panel in der Seitenleiste anzeigen",
	"account.sidebar_panel_body": "Ausgeschaltet verschwindet das Panel für alle Benutzer.",
	"account.save": "Speichern",
	"account.saved": "Gespeichert",
	"account.reload_warning": "Beim Speichern wird die Verbindung zu Kohler für einige Sekunden neu aufgebaut.",
	"dialog.cancel": "Abbrechen",
	"dialog.confirm": "Bestätigen",
	"control.decrease": "{name} verringern",
	"control.increase": "{name} erhöhen",
	"generic.controls": "Steuerung",
	"generic.readings": "Messwerte",
	"generic.press": "Drücken",
	"generic.press_named": "{name} drücken",
	"generic.brightness": "{name}: Helligkeit",
	"generic.effect": "{name}: Effekt",
	"generic.untested_body": "Diese Steuerungen entsprechen genau der Konnect-App, wurden aber noch nicht an echter Hardware bestätigt.",
	"generic.ozone_title": "Bitte nicht duschen",
	"generic.ozone_body": "Eine Ozonreinigung leitet ozonhaltiges Wasser durch die Dusche, um sie zu reinigen. Stelle sicher, dass niemand in der Dusche ist und bis zum Ende der Reinigung draußen bleibt.",
	"generic.ozone_confirm": "Reinigung starten",
	"unit.minutes_short": "{count} min",
	"unit.hours_minutes": "{hours} h {minutes} min",
	"smart_fan.fan": "Lüfter",
	"smart_fan.light": "Licht",
	"smart_fan.on": "An",
	"smart_fan.fan_on": "Lüfter an",
	"smart_fan.light_on": "Licht an",
	"smart_fan.fan_favorites": "Lüfter-Favoriten",
	"smart_fan.light_favorites": "Licht-Favoriten",
	"smart_fan.stop_all": "Alles stoppen",
	"smart_fan.locked_running": "Einstellungen für Lüfter und Licht lassen sich nicht ändern, solange sie eingeschaltet sind.",
	"smart_fan.defaults_body": "Was Lüfter und Licht tun, wenn sie ohne Favorit gestartet werden.",
	"smart_fan.fan_speed": "Lüfterstufe",
	"smart_fan.fan_timer": "Lüftertimer",
	"smart_fan.fan_timer_body": "Schaltet den Lüfter nach Ablauf seiner Laufzeit ab.",
	"smart_fan.fan_run_time": "Laufzeit des Lüfters",
	"smart_fan.light_color": "Lichtfarbe",
	"smart_fan.light_brightness": "Lichthelligkeit",
	"smart_fan.circadian": "Zirkadiane Beleuchtung",
	"smart_fan.circadian_body": "Das Licht folgt der Tageszeit statt einer festen Farbe.",
	"smart_fan.audible_feedback": "Akustische Rückmeldung",
	"smart_fan.audible_feedback_body": "Piept bei Aktionen über Fernbedienung und App.",
	"smart_fan.automation": "Lüfterautomatik",
	"smart_fan.humidity": "Feuchtigkeitserkennung",
	"smart_fan.humidity_body": "Startet den Lüfter, wenn die Luft feucht wird.",
	"smart_fan.sensitivity": "Empfindlichkeit",
	"smart_fan.schedule": "Lüfterzeitplan",
	"smart_fan.schedule_body": "Lege einen mit der Aktion set_fan_schedule oder in der Konnect-App an; hier schaltest du ihn ein oder aus.",
	"smart_fan.every_day": "Jeden Tag",
	"smart_fan.once": "Einmal",
	"toilet.cleaning": "Reinigung",
	"toilet.occupied": "Besetzt",
	"ice_bath.week_of": "Diese Woche: {done} von {target}",
	"ice_bath.week": "Diese Woche: {done}"
}, Ye = {
	"app.title": "Kohler Konnect",
	"app.loading": "Connecting to Kohler Konnect…",
	"app.no_devices": "No Kohler devices yet",
	"app.no_devices_body": "Devices on your Kohler Konnect account appear here once the integration has read them.",
	"app.stale_bundle": "The panel was updated. Refresh to load the new version.",
	"app.refresh": "Refresh",
	"app.not_found": "This device is no longer on the account.",
	"nav.back": "Back",
	"nav.more": "More",
	"nav.menu": "Menu",
	"nav.controls": "Controls",
	"nav.favorites": "Favorites",
	"nav.experiences": "Experiences",
	"nav.devices": "Devices",
	"nav.usage": "Usage",
	"nav.account": "Account",
	"home.welcome": "Welcome, {name}",
	"home.welcome_anonymous": "Welcome",
	"home.help": "Need help?",
	"home.summary_quiet": "All quiet",
	"home.summary_running": "{count} running",
	"home.summary_alert": "Needs attention: {count}",
	"home.other_area": "Other",
	"home.usage_this_month": "This month",
	"home.usage_today": "Today",
	"home.usage_last_7_days": "Last 7 days",
	"status.idle": "Idle",
	"status.ready": "Ready",
	"status.off": "Off",
	"status.on": "On",
	"status.offline": "Offline",
	"status.running": "Running",
	"status.warming": "Warming up",
	"status.paused": "Paused",
	"status.water_on": "Water on",
	"status.dispensing": "Dispensing",
	"status.leak": "Leak detected",
	"status.problem": "Needs attention",
	"status.minutes_left": "{time} left",
	"status.untested": "Untested",
	"shower.simple_start": "Simple Start",
	"shower.simple_start_unknown": "The valve hasn't reported its default shower yet",
	"shower.outlets": "Outlets",
	"shower.zone": "Zone {zone}",
	"shower.temperature": "Temperature",
	"shower.flow": "Flow",
	"shower.start": "Start",
	"shower.stop": "Stop",
	"shower.resume": "Resume",
	"shower.cold": "Cold",
	"shower.staged_hint": "Choose outlets, then press Start",
	"shower.locked_warming": "Changes are locked while the valve warms up",
	"shower.locked": "Locked",
	"shower.set_to": "Set to {temperature}",
	"shower.at_temperature": "At temperature",
	"shower.heating": "Reaching temperature",
	"shower.warming_title": "Warming up",
	"shower.running_title": "Water running",
	"shower.paused_title": "Ready when you are",
	"shower.paused_body": "Warm-up finished. Resume to start your shower.",
	"shower.time_left": "left",
	"scenes.empty_favorites": "No favorites yet",
	"scenes.empty_favorites_body": "Favorites made in the Konnect app or at the touchscreen appear here.",
	"scenes.empty_experiences": "No experiences yet",
	"scenes.empty_experiences_body": "Experiences added in the Konnect app appear here.",
	"scenes.start": "Start",
	"scenes.running": "Running",
	"controller.steam": "Steam",
	"controller.steam_start": "Start steam",
	"controller.steam_stop": "Stop steam",
	"controller.steam_blocked": "Steam can't run while the shower does",
	"controller.status": "Status",
	"controller.music": "Music",
	"controller.light": "Light",
	"controller.read_only": "Read-only: the controller runs favorites, not individual outlets.",
	"faucet.faucet": "Faucet",
	"faucet.dispense_amount": "Dispense Amount",
	"faucet.start": "Start",
	"faucet.stop": "Stop",
	"faucet.see_more": "See more",
	"faucet.see_less": "See less",
	"faucet.presets": "Presets",
	"faucet.no_presets": "Presets made in the Konnect app appear here.",
	"faucet.custom": "Custom amount",
	"faucet.auto_off": "Turns off automatically in {time}",
	"faucet.handle_closed": "Open the handle to run water remotely.",
	"faucet.firmware_downloading": "The faucet is downloading firmware. Water can't be started until it finishes.",
	"faucet.leak": "Kohler reported a leak at this faucet.",
	"faucet.clear_leak": "Clear",
	"faucet.last_dispensed": "Last dispensed {amount}",
	"settings.title": "Settings",
	"settings.locked_running": "Settings can't be changed while water is running.",
	"settings.defaults": "Default Settings",
	"settings.defaults_body": "Values set here are used by default when you start your shower.",
	"settings.default_temperature": "Temperature",
	"settings.default_outlets": "Outlets",
	"settings.default_outlets_body": "Change the default outlets in the Konnect app.",
	"settings.device_control": "Device Control",
	"settings.warmup": "Shower Warmup",
	"settings.warmup_body": "Warms the water before the shower starts, then waits until you're ready.",
	"settings.warmup_outlets": "Outlets to warm",
	"settings.warmup_auto_restore": "Warmup Auto-Restore",
	"settings.warmup_auto_restore_body": "Puts warm-up back when something silently turns it off.",
	"settings.max_temperature": "Max Temperature",
	"settings.max_duration": "Max Shower Duration",
	"settings.flow": "Flow",
	"settings.general": "General",
	"settings.about": "About",
	"settings.water_usage": "Water Usage",
	"settings.diagnostics": "Diagnostics",
	"settings.restart": "Restart Product",
	"settings.save": "Save",
	"settings.cancel": "Cancel",
	"settings.dispense_defaults": "Dispensing",
	"settings.dispense_amount": "Dispense amount",
	"settings.preset": "Preset",
	"settings.safety_limit": "Water safety limit",
	"settings.safety_limit_body": "Set for the whole account under Account.",
	"settings.web_settings": "Controller web settings",
	"settings.scald_title": "Change the scald limit?",
	"settings.scald_body": "Max Temperature is the hottest water this shower can deliver. Raising it to {temperature} lets every outlet run that hot.",
	"settings.scald_confirm": "Change limit",
	"settings.restart_title": "Restart {name}?",
	"settings.restart_body": "The valve reboots, as the Konnect app's Restart Product does. Any running water stops.",
	"settings.restart_confirm": "Restart",
	"settings.restart_done": "Restart sent.",
	"settings.touchscreen": "Touchscreen",
	"settings.touchscreen_body": "Each change is written to every touch panel at once, as the Konnect app's Control Panel does.",
	"settings.panel_language": "Panel language",
	"settings.panel_units": "Panel units",
	"settings.wake_screen": "Wake screen",
	"settings.display_brightness": "Display brightness",
	"settings.haptic_feedback": "Haptic feedback",
	"settings.accessible_display": "Accessible display",
	"settings.accessible_display_body": "Adds on-screen controls that mirror the dial. The panels may take a few moments to update.",
	"settings.flow_control": "Flow control",
	"settings.flow_control_body": "Lets the flow be adjusted on the panel's main screen.",
	"settings.water_saving_mode": "Water-saving mode",
	"settings.water_saving_mode_body": "Set when the shower was installed: one outlet at a time. The Konnect app doesn't change it either.",
	"about.title": "About",
	"about.model": "Model",
	"about.serial": "Serial Number",
	"about.install_date": "Install Date",
	"about.registered": "Registered",
	"about.hardware": "Hardware",
	"about.firmware": "Firmware",
	"about.firmware.interface": "Interface",
	"about.firmware.valve": "Valve",
	"about.firmware.second_valve": "Second valve",
	"about.firmware.gateway": "Gateway",
	"about.firmware.controller": "Controller",
	"about.firmware.application": "Application",
	"about.firmware.bootloader": "Bootloader",
	"about.firmware.os": "Operating system",
	"about.firmware.certificate": "Certificate",
	"about.firmware.service_pack": "Service pack",
	"about.up_to_date": "Up to date",
	"about.update_available": "Update available: {version}",
	"about.update_in_app": "Install updates from the Konnect app.",
	"about.technical": "Technical Information",
	"about.gateway": "Gateway",
	"about.valves": "Valves",
	"about.interfaces": "Interfaces",
	"about.status": "Status",
	"about.leak_history": "Leak history",
	"about.no_leaks": "No leaks reported",
	"about.unknown": "Not reported",
	"usage.title": "Water Usage",
	"usage.week": "Week",
	"usage.month": "Month",
	"usage.year": "Year",
	"usage.average": "Average",
	"usage.high": "High",
	"usage.total": "Total",
	"usage.water": "Water",
	"usage.time": "Time",
	"usage.per_day": "per day",
	"usage.previous": "Previous period",
	"usage.next": "Next period",
	"usage.calendar_week": "Calendar week. The This Week sensors cover the last seven days.",
	"usage.error": "Kohler didn't return usage for this period.",
	"usage.retry": "Try again",
	"usage.equivalent_bath": "About {count} full baths of water",
	"diagnostics.title": "Diagnostics",
	"diagnostics.stream": "Kohler connection",
	"diagnostics.stream_body": "Home Assistant's live link to Kohler's cloud.",
	"diagnostics.cloud": "Device online",
	"diagnostics.cloud_body": "Whether Kohler's cloud can reach this device.",
	"diagnostics.connected": "Connected",
	"diagnostics.disconnected": "Disconnected",
	"diagnostics.unknown": "Not checked yet",
	"diagnostics.last_update": "Last update",
	"diagnostics.faults": "Active faults",
	"diagnostics.no_faults": "No active faults",
	"diagnostics.clear_errors": "Clear error log",
	"diagnostics.report_log": "Report Log",
	"diagnostics.report_log_body": "Records every Kohler message for a bug report.",
	"diagnostics.download": "Download diagnostics",
	"diagnostics.download_body": "One report of the whole installation, with credentials and serial numbers redacted.",
	"account.admin_only": "Account settings are available to administrators.",
	"account.connection": "Kohler connection",
	"account.zone_grouping": "Zone grouping",
	"account.zone_grouping.numbered": "Zone numbers",
	"account.zone_grouping.subdevices": "Sub-device per zone",
	"account.zone_grouping.outlet_labels": "Outlet names on controls",
	"account.safety_limit": "Water safety limit",
	"account.safety_limit_body": "Turns faucet water off this long after Home Assistant turns it on. 0 turns the limit off.",
	"account.minutes": "{count} min",
	"account.off": "Off",
	"account.sidebar_panel": "Show this panel in the sidebar",
	"account.sidebar_panel_body": "Turning it off removes the panel for every user.",
	"account.save": "Save",
	"account.saved": "Saved",
	"account.reload_warning": "Saving reconnects to Kohler for a few seconds.",
	"dialog.cancel": "Cancel",
	"dialog.confirm": "Confirm",
	"control.decrease": "Decrease {name}",
	"control.increase": "Increase {name}",
	"generic.controls": "Controls",
	"generic.readings": "Readings",
	"generic.press": "Press",
	"generic.press_named": "Press {name}",
	"generic.brightness": "{name} brightness",
	"generic.effect": "{name} effect",
	"generic.untested_body": "These controls follow the Konnect app exactly but haven't been confirmed on real hardware yet.",
	"generic.ozone_title": "Stay out of the shower",
	"generic.ozone_body": "An ozone clean sends ozonated water through the shower to clean it. Make sure nobody is in the shower, and stays out until the clean has finished.",
	"generic.ozone_confirm": "Start clean",
	"unit.minutes_short": "{count} min",
	"unit.hours_minutes": "{hours} h {minutes} min",
	"smart_fan.fan": "Fan",
	"smart_fan.light": "Light",
	"smart_fan.on": "On",
	"smart_fan.fan_on": "Fan on",
	"smart_fan.light_on": "Light on",
	"smart_fan.fan_favorites": "Fan favorites",
	"smart_fan.light_favorites": "Light favorites",
	"smart_fan.stop_all": "Stop all",
	"smart_fan.locked_running": "A setting for the fan or light can't change while it runs.",
	"smart_fan.defaults_body": "What the fan and light do when started without a favorite.",
	"smart_fan.fan_speed": "Fan speed",
	"smart_fan.fan_timer": "Fan timer",
	"smart_fan.fan_timer_body": "Turns the fan off after its run time.",
	"smart_fan.fan_run_time": "Fan run time",
	"smart_fan.light_color": "Light color",
	"smart_fan.light_brightness": "Light brightness",
	"smart_fan.circadian": "Circadian lighting",
	"smart_fan.circadian_body": "The light follows the time of day instead of a set color.",
	"smart_fan.audible_feedback": "Audible feedback",
	"smart_fan.audible_feedback_body": "Beeps for remote and app actions.",
	"smart_fan.automation": "Fan Automation",
	"smart_fan.humidity": "Humidity sensing",
	"smart_fan.humidity_body": "Starts the fan when the air gets humid.",
	"smart_fan.sensitivity": "Sensitivity",
	"smart_fan.schedule": "Fan schedule",
	"smart_fan.schedule_body": "Set one up with the set_fan_schedule action or in the Konnect app; turn it on or off here.",
	"smart_fan.every_day": "Every day",
	"smart_fan.once": "Once",
	"toilet.cleaning": "Cleaning",
	"toilet.occupied": "In use",
	"ice_bath.week_of": "This week: {done} of {target}",
	"ice_bath.week": "This week: {done}"
}, Xe = {
	"app.title": "Kohler Konnect",
	"app.loading": "Conectando con Kohler Konnect…",
	"app.no_devices": "Todavía no hay dispositivos Kohler",
	"app.no_devices_body": "Los dispositivos de tu cuenta de Kohler Konnect aparecerán aquí cuando la integración los haya leído.",
	"app.stale_bundle": "El panel se actualizó. Recarga la página para usar la versión nueva.",
	"app.refresh": "Recargar",
	"app.not_found": "Este dispositivo ya no está en la cuenta.",
	"nav.back": "Atrás",
	"nav.more": "Más opciones",
	"nav.menu": "Menú",
	"nav.controls": "Controles",
	"nav.favorites": "Favoritos",
	"nav.experiences": "Experiencias",
	"nav.devices": "Dispositivos",
	"nav.usage": "Consumo",
	"nav.account": "Cuenta",
	"home.welcome": "Hola, {name}",
	"home.welcome_anonymous": "Hola",
	"home.help": "¿Necesitas ayuda?",
	"home.summary_quiet": "Todo tranquilo",
	"home.summary_running": "En marcha: {count}",
	"home.summary_alert": "Requiere atención: {count}",
	"home.other_area": "Otros",
	"home.usage_this_month": "Este mes",
	"home.usage_today": "Hoy",
	"home.usage_last_7_days": "Últimos 7 días",
	"status.idle": "Inactivo",
	"status.ready": "Listo",
	"status.off": "Apagado",
	"status.on": "Encendido",
	"status.offline": "Sin conexión",
	"status.running": "En marcha",
	"status.warming": "Precalentando",
	"status.paused": "En pausa",
	"status.water_on": "Agua abierta",
	"status.dispensing": "Dispensando",
	"status.leak": "Fuga detectada",
	"status.problem": "Requiere atención",
	"status.minutes_left": "Quedan {time}",
	"status.untested": "Sin probar",
	"shower.simple_start": "Inicio simple",
	"shower.simple_start_unknown": "La válvula todavía no ha informado su ducha predeterminada",
	"shower.outlets": "Salidas",
	"shower.zone": "Zona {zone}",
	"shower.temperature": "Temperatura",
	"shower.flow": "Caudal",
	"shower.start": "Iniciar",
	"shower.stop": "Detener",
	"shower.resume": "Reanudar",
	"shower.cold": "Fría",
	"shower.staged_hint": "Elige las salidas y presiona Iniciar",
	"shower.locked_warming": "Los cambios están bloqueados mientras la válvula se precalienta",
	"shower.locked": "Bloqueado",
	"shower.set_to": "Ajustada a {temperature}",
	"shower.at_temperature": "Temperatura alcanzada",
	"shower.heating": "Alcanzando la temperatura",
	"shower.warming_title": "Precalentando",
	"shower.running_title": "Agua abierta",
	"shower.paused_title": "Todo listo cuando quieras",
	"shower.paused_body": "Precalentamiento terminado. Reanuda para iniciar tu ducha.",
	"shower.time_left": "restante",
	"scenes.empty_favorites": "Todavía no hay favoritos",
	"scenes.empty_favorites_body": "Los favoritos creados en la app Konnect o en la pantalla táctil aparecen aquí.",
	"scenes.empty_experiences": "Todavía no hay experiencias",
	"scenes.empty_experiences_body": "Las experiencias agregadas en la app Konnect aparecen aquí.",
	"scenes.start": "Iniciar",
	"scenes.running": "En marcha",
	"controller.steam": "Vapor",
	"controller.steam_start": "Iniciar vapor",
	"controller.steam_stop": "Detener vapor",
	"controller.steam_blocked": "El vapor no puede funcionar con la ducha en marcha",
	"controller.status": "Estado",
	"controller.music": "Música",
	"controller.light": "Luz",
	"controller.read_only": "Solo lectura: el controlador ejecuta favoritos, no salidas individuales.",
	"faucet.faucet": "Llave",
	"faucet.dispense_amount": "Cantidad a dispensar",
	"faucet.start": "Iniciar",
	"faucet.stop": "Detener",
	"faucet.see_more": "Ver más",
	"faucet.see_less": "Ver menos",
	"faucet.presets": "Preajustes",
	"faucet.no_presets": "Los preajustes creados en la app Konnect aparecen aquí.",
	"faucet.custom": "Cantidad personalizada",
	"faucet.auto_off": "Se cierra automáticamente en {time}",
	"faucet.handle_closed": "Abre la manija para usar el agua a distancia.",
	"faucet.firmware_downloading": "La llave está descargando el firmware. No se puede abrir el agua hasta que termine.",
	"faucet.leak": "Kohler informó una fuga en esta llave.",
	"faucet.clear_leak": "Borrar",
	"faucet.last_dispensed": "Último dispensado: {amount}",
	"settings.title": "Configuración",
	"settings.locked_running": "La configuración no se puede cambiar mientras corre el agua.",
	"settings.defaults": "Configuración predeterminada",
	"settings.defaults_body": "Los valores que configures aquí se aplican por defecto al iniciar la ducha.",
	"settings.default_temperature": "Temperatura",
	"settings.default_outlets": "Salidas",
	"settings.default_outlets_body": "Cambia las salidas predeterminadas en la app Konnect.",
	"settings.device_control": "Control del dispositivo",
	"settings.warmup": "Precalentamiento",
	"settings.warmup_body": "Calienta el agua antes de la ducha y luego espera hasta que quieras empezar.",
	"settings.warmup_outlets": "Salidas que precalentar",
	"settings.warmup_auto_restore": "Restaurar precalentamiento",
	"settings.warmup_auto_restore_body": "Vuelve a activar el precalentamiento si algo lo desactiva sin avisar.",
	"settings.max_temperature": "Temperatura máxima",
	"settings.max_duration": "Duración máxima de la ducha",
	"settings.flow": "Caudal",
	"settings.general": "General",
	"settings.about": "Acerca de",
	"settings.water_usage": "Consumo de agua",
	"settings.diagnostics": "Diagnósticos",
	"settings.restart": "Reiniciar producto",
	"settings.save": "Guardar",
	"settings.cancel": "Cancelar",
	"settings.dispense_defaults": "Dispensación",
	"settings.dispense_amount": "Cantidad a dispensar",
	"settings.preset": "Preajuste",
	"settings.safety_limit": "Límite de seguridad del agua",
	"settings.safety_limit_body": "Se configura para toda la cuenta en Cuenta.",
	"settings.web_settings": "Configuración web del controlador",
	"settings.scald_title": "¿Cambiar el límite antiquemaduras?",
	"settings.scald_body": "La temperatura máxima es el agua más caliente que puede dar esta ducha. Si la subes a {temperature}, todas las salidas podrán alcanzar esa temperatura.",
	"settings.scald_confirm": "Cambiar límite",
	"settings.restart_title": "¿Reiniciar {name}?",
	"settings.restart_body": "La válvula se reinicia, como hace la opción Reiniciar producto de la app Konnect. Si hay agua corriendo, se corta.",
	"settings.restart_confirm": "Reiniciar",
	"settings.restart_done": "Reinicio enviado.",
	"settings.touchscreen": "Pantalla táctil",
	"settings.touchscreen_body": "Cada cambio se escribe en todos los paneles a la vez, como hace el Panel de control de la app Konnect.",
	"settings.panel_language": "Idioma del panel",
	"settings.panel_units": "Unidades del panel",
	"settings.wake_screen": "Pantalla al activarse",
	"settings.display_brightness": "Brillo de la pantalla",
	"settings.haptic_feedback": "Respuesta háptica",
	"settings.accessible_display": "Pantalla accesible",
	"settings.accessible_display_body": "Añade controles en pantalla que replican el dial. Los paneles pueden tardar unos momentos en actualizarse.",
	"settings.flow_control": "Control del caudal",
	"settings.flow_control_body": "Permite ajustar el caudal en la pantalla principal del panel.",
	"settings.water_saving_mode": "Modo de ahorro de agua",
	"settings.water_saving_mode_body": "Se fija al instalar la ducha: una salida a la vez. La app Konnect tampoco lo cambia.",
	"about.title": "Acerca de",
	"about.model": "Modelo",
	"about.serial": "Número de serie",
	"about.install_date": "Fecha de instalación",
	"about.registered": "Registrado",
	"about.hardware": "Hardware",
	"about.firmware": "Firmware",
	"about.firmware.interface": "Interfaz",
	"about.firmware.valve": "Válvula",
	"about.firmware.second_valve": "Segunda válvula",
	"about.firmware.gateway": "Puerta de enlace",
	"about.firmware.controller": "Controlador",
	"about.firmware.application": "Aplicación",
	"about.firmware.bootloader": "Bootloader",
	"about.firmware.os": "Sistema operativo",
	"about.firmware.certificate": "Certificado",
	"about.firmware.service_pack": "Service pack",
	"about.up_to_date": "Actualizado",
	"about.update_available": "Actualización disponible: {version}",
	"about.update_in_app": "Instala las actualizaciones desde la app Konnect.",
	"about.technical": "Información técnica",
	"about.gateway": "Puerta de enlace",
	"about.valves": "Válvulas",
	"about.interfaces": "Interfaces",
	"about.status": "Estado",
	"about.leak_history": "Historial de fugas",
	"about.no_leaks": "Sin fugas reportadas",
	"about.unknown": "No informado",
	"usage.title": "Consumo de agua",
	"usage.week": "Semana",
	"usage.month": "Mes",
	"usage.year": "Año",
	"usage.average": "Promedio",
	"usage.high": "Máximo",
	"usage.total": "Total",
	"usage.water": "Agua",
	"usage.time": "Tiempo",
	"usage.per_day": "por día",
	"usage.previous": "Período anterior",
	"usage.next": "Período siguiente",
	"usage.calendar_week": "Semana calendario. Los sensores semanales abarcan los últimos siete días.",
	"usage.error": "Kohler no devolvió el consumo de este período.",
	"usage.retry": "Reintentar",
	"usage.equivalent_bath": "Tinas llenas de agua: cerca de {count}",
	"diagnostics.title": "Diagnósticos",
	"diagnostics.stream": "Conexión con Kohler",
	"diagnostics.stream_body": "La conexión en tiempo real de Home Assistant con la nube de Kohler.",
	"diagnostics.cloud": "Dispositivo en línea",
	"diagnostics.cloud_body": "Si la nube de Kohler llega a este dispositivo.",
	"diagnostics.connected": "Conectado",
	"diagnostics.disconnected": "Desconectado",
	"diagnostics.unknown": "Todavía sin verificar",
	"diagnostics.last_update": "Última actualización",
	"diagnostics.faults": "Errores activos",
	"diagnostics.no_faults": "No hay errores activos",
	"diagnostics.clear_errors": "Borrar registro de errores",
	"diagnostics.report_log": "Report Log",
	"diagnostics.report_log_body": "Registra todos los mensajes de Kohler para reportar un error.",
	"diagnostics.download": "Descargar diagnósticos",
	"diagnostics.download_body": "Un reporte de toda la instalación, con las credenciales y los números de serie ocultos.",
	"account.admin_only": "La configuración de la cuenta solo está disponible para administradores.",
	"account.connection": "Conexión con Kohler",
	"account.zone_grouping": "Agrupación de zonas",
	"account.zone_grouping.numbered": "Números de zona",
	"account.zone_grouping.subdevices": "Un subdispositivo por zona",
	"account.zone_grouping.outlet_labels": "Nombres de salidas en los controles",
	"account.safety_limit": "Límite de seguridad del agua",
	"account.safety_limit_body": "Apaga el agua de la llave este tiempo después de que Home Assistant la encienda. 0 desactiva el límite.",
	"account.minutes": "{count} min",
	"account.off": "Apagado",
	"account.sidebar_panel": "Mostrar este panel en la barra lateral",
	"account.sidebar_panel_body": "Al desactivarlo, el panel desaparece para todos los usuarios.",
	"account.save": "Guardar",
	"account.saved": "Guardado",
	"account.reload_warning": "Al guardar, la conexión con Kohler se reinicia por unos segundos.",
	"dialog.cancel": "Cancelar",
	"dialog.confirm": "Confirmar",
	"control.decrease": "Reducir {name}",
	"control.increase": "Aumentar {name}",
	"generic.controls": "Controles",
	"generic.readings": "Lecturas",
	"generic.press": "Presionar",
	"generic.press_named": "Presionar {name}",
	"generic.brightness": "{name}: brillo",
	"generic.effect": "{name}: efecto",
	"generic.untested_body": "Estos controles replican exactamente la app Konnect, pero todavía no se han confirmado en hardware real.",
	"generic.ozone_title": "No entres a la regadera",
	"generic.ozone_body": "Una limpieza con ozono hace pasar agua ozonizada por la regadera para limpiarla. Asegúrate de que no haya nadie en la regadera y de que nadie entre hasta que termine.",
	"generic.ozone_confirm": "Iniciar limpieza",
	"unit.minutes_short": "{count} min",
	"unit.hours_minutes": "{hours} h {minutes} min",
	"smart_fan.fan": "Ventilador",
	"smart_fan.light": "Luz",
	"smart_fan.on": "Encendido",
	"smart_fan.fan_on": "Ventilador encendido",
	"smart_fan.light_on": "Luz encendida",
	"smart_fan.fan_favorites": "Favoritos del ventilador",
	"smart_fan.light_favorites": "Favoritos de la luz",
	"smart_fan.stop_all": "Detener todo",
	"smart_fan.locked_running": "No se puede cambiar un ajuste del ventilador o de la luz mientras está funcionando.",
	"smart_fan.defaults_body": "Lo que hacen el ventilador y la luz al encenderse sin un favorito.",
	"smart_fan.fan_speed": "Velocidad del ventilador",
	"smart_fan.fan_timer": "Temporizador del ventilador",
	"smart_fan.fan_timer_body": "Apaga el ventilador cuando termina su tiempo de funcionamiento.",
	"smart_fan.fan_run_time": "Tiempo de funcionamiento",
	"smart_fan.light_color": "Color de luz",
	"smart_fan.light_brightness": "Brillo de luz",
	"smart_fan.circadian": "Iluminación circadiana",
	"smart_fan.circadian_body": "La luz sigue la hora del día en lugar de un color fijo.",
	"smart_fan.audible_feedback": "Confirmación sonora",
	"smart_fan.audible_feedback_body": "Emite pitidos con las acciones del control remoto y de la app.",
	"smart_fan.automation": "Automatización del ventilador",
	"smart_fan.humidity": "Detección de humedad",
	"smart_fan.humidity_body": "Enciende el ventilador cuando sube la humedad del aire.",
	"smart_fan.sensitivity": "Sensibilidad",
	"smart_fan.schedule": "Programación del ventilador",
	"smart_fan.schedule_body": "Créala con la acción set_fan_schedule o en la app Konnect; actívala o desactívala aquí.",
	"smart_fan.every_day": "Todos los días",
	"smart_fan.once": "Una vez",
	"toilet.cleaning": "Limpiando",
	"toilet.occupied": "En uso",
	"ice_bath.week_of": "Esta semana: {done} de {target}",
	"ice_bath.week": "Esta semana: {done}"
}, Ze = {
	"app.title": "Kohler Konnect",
	"app.loading": "Conectando con Kohler Konnect…",
	"app.no_devices": "Aún no hay dispositivos Kohler",
	"app.no_devices_body": "Los dispositivos de tu cuenta de Kohler Konnect aparecerán aquí cuando la integración los haya leído.",
	"app.stale_bundle": "El panel se ha actualizado. Recarga la página para usar la nueva versión.",
	"app.refresh": "Recargar",
	"app.not_found": "Este dispositivo ya no está en la cuenta.",
	"nav.back": "Atrás",
	"nav.more": "Más opciones",
	"nav.menu": "Menú",
	"nav.controls": "Controles",
	"nav.favorites": "Favoritos",
	"nav.experiences": "Experiencias",
	"nav.devices": "Dispositivos",
	"nav.usage": "Consumo",
	"nav.account": "Cuenta",
	"home.welcome": "Hola, {name}",
	"home.welcome_anonymous": "Hola",
	"home.help": "¿Necesitas ayuda?",
	"home.summary_quiet": "Todo en calma",
	"home.summary_running": "En marcha: {count}",
	"home.summary_alert": "Requiere atención: {count}",
	"home.other_area": "Otros",
	"home.usage_this_month": "Este mes",
	"home.usage_today": "Hoy",
	"home.usage_last_7_days": "Últimos 7 días",
	"status.idle": "Inactivo",
	"status.ready": "Listo",
	"status.off": "Apagado",
	"status.on": "Encendido",
	"status.offline": "Desconectado",
	"status.running": "En marcha",
	"status.warming": "Precalentando",
	"status.paused": "En pausa",
	"status.water_on": "Agua abierta",
	"status.dispensing": "Dispensando",
	"status.leak": "Fuga detectada",
	"status.problem": "Requiere atención",
	"status.minutes_left": "Quedan {time}",
	"status.untested": "Sin probar",
	"shower.simple_start": "Inicio simple",
	"shower.simple_start_unknown": "La válvula aún no ha informado de su ducha predeterminada",
	"shower.outlets": "Salidas",
	"shower.zone": "Zona {zone}",
	"shower.temperature": "Temperatura",
	"shower.flow": "Caudal",
	"shower.start": "Iniciar",
	"shower.stop": "Detener",
	"shower.resume": "Reanudar",
	"shower.cold": "Fría",
	"shower.staged_hint": "Elige las salidas y pulsa Iniciar",
	"shower.locked_warming": "Los cambios están bloqueados mientras la válvula se precalienta",
	"shower.locked": "Bloqueado",
	"shower.set_to": "Ajustada a {temperature}",
	"shower.at_temperature": "Temperatura alcanzada",
	"shower.heating": "Alcanzando la temperatura",
	"shower.warming_title": "Precalentando",
	"shower.running_title": "Agua abierta",
	"shower.paused_title": "Todo listo cuando quieras",
	"shower.paused_body": "Precalentamiento terminado. Reanuda para empezar a ducharte.",
	"shower.time_left": "restante",
	"scenes.empty_favorites": "Aún no hay favoritos",
	"scenes.empty_favorites_body": "Los favoritos creados en la app Konnect o en la pantalla táctil aparecen aquí.",
	"scenes.empty_experiences": "Aún no hay experiencias",
	"scenes.empty_experiences_body": "Las experiencias añadidas en la app Konnect aparecen aquí.",
	"scenes.start": "Iniciar",
	"scenes.running": "En marcha",
	"controller.steam": "Vapor",
	"controller.steam_start": "Iniciar vapor",
	"controller.steam_stop": "Detener vapor",
	"controller.steam_blocked": "El vapor no puede funcionar con la ducha en marcha",
	"controller.status": "Estado",
	"controller.music": "Música",
	"controller.light": "Luz",
	"controller.read_only": "Solo lectura: el controlador ejecuta favoritos, no salidas individuales.",
	"faucet.faucet": "Grifo",
	"faucet.dispense_amount": "Cantidad a dispensar",
	"faucet.start": "Iniciar",
	"faucet.stop": "Detener",
	"faucet.see_more": "Ver más",
	"faucet.see_less": "Ver menos",
	"faucet.presets": "Preajustes",
	"faucet.no_presets": "Los preajustes creados en la app Konnect aparecen aquí.",
	"faucet.custom": "Cantidad personalizada",
	"faucet.auto_off": "Se cierra automáticamente en {time}",
	"faucet.handle_closed": "Abre la maneta para usar el agua a distancia.",
	"faucet.firmware_downloading": "El grifo está descargando el firmware. No se puede abrir el agua hasta que termine.",
	"faucet.leak": "Kohler ha informado de una fuga en este grifo.",
	"faucet.clear_leak": "Borrar",
	"faucet.last_dispensed": "Último dispensado: {amount}",
	"settings.title": "Ajustes",
	"settings.locked_running": "Los ajustes no se pueden cambiar mientras corre el agua.",
	"settings.defaults": "Ajustes predeterminados",
	"settings.defaults_body": "Los valores que definas aquí se aplican por defecto al iniciar la ducha.",
	"settings.default_temperature": "Temperatura",
	"settings.default_outlets": "Salidas",
	"settings.default_outlets_body": "Cambia las salidas predeterminadas en la app Konnect.",
	"settings.device_control": "Control del dispositivo",
	"settings.warmup": "Precalentamiento",
	"settings.warmup_body": "Calienta el agua antes de la ducha y luego espera hasta que quieras empezar.",
	"settings.warmup_outlets": "Salidas que precalentar",
	"settings.warmup_auto_restore": "Restaurar precalentamiento",
	"settings.warmup_auto_restore_body": "Vuelve a activar el precalentamiento si algo lo desactiva sin avisar.",
	"settings.max_temperature": "Temperatura máxima",
	"settings.max_duration": "Duración máxima de la ducha",
	"settings.flow": "Caudal",
	"settings.general": "General",
	"settings.about": "Acerca de",
	"settings.water_usage": "Consumo de agua",
	"settings.diagnostics": "Diagnósticos",
	"settings.restart": "Reiniciar producto",
	"settings.save": "Guardar",
	"settings.cancel": "Cancelar",
	"settings.dispense_defaults": "Dispensación",
	"settings.dispense_amount": "Cantidad a dispensar",
	"settings.preset": "Preajuste",
	"settings.safety_limit": "Límite de seguridad del agua",
	"settings.safety_limit_body": "Se configura para toda la cuenta en Cuenta.",
	"settings.web_settings": "Ajustes web del controlador",
	"settings.scald_title": "¿Cambiar el límite antiquemaduras?",
	"settings.scald_body": "La temperatura máxima es el agua más caliente que puede dar esta ducha. Si la subes a {temperature}, todas las salidas podrán alcanzar esa temperatura.",
	"settings.scald_confirm": "Cambiar límite",
	"settings.restart_title": "¿Reiniciar {name}?",
	"settings.restart_body": "La válvula se reinicia, como hace la opción Reiniciar producto de la app Konnect. Si hay agua corriendo, se corta.",
	"settings.restart_confirm": "Reiniciar",
	"settings.restart_done": "Reinicio enviado.",
	"settings.touchscreen": "Pantalla táctil",
	"settings.touchscreen_body": "Cada cambio se escribe en todos los paneles a la vez, como hace el Panel de control de la app Konnect.",
	"settings.panel_language": "Idioma del panel",
	"settings.panel_units": "Unidades del panel",
	"settings.wake_screen": "Pantalla al activarse",
	"settings.display_brightness": "Brillo de la pantalla",
	"settings.haptic_feedback": "Respuesta háptica",
	"settings.accessible_display": "Pantalla accesible",
	"settings.accessible_display_body": "Añade controles en pantalla que replican el dial. Los paneles pueden tardar unos momentos en actualizarse.",
	"settings.flow_control": "Control del caudal",
	"settings.flow_control_body": "Permite ajustar el caudal en la pantalla principal del panel.",
	"settings.water_saving_mode": "Modo de ahorro de agua",
	"settings.water_saving_mode_body": "Se fija al instalar la ducha: una salida a la vez. La app Konnect tampoco lo cambia.",
	"about.title": "Acerca de",
	"about.model": "Modelo",
	"about.serial": "Número de serie",
	"about.install_date": "Fecha de instalación",
	"about.registered": "Registrado",
	"about.hardware": "Hardware",
	"about.firmware": "Firmware",
	"about.firmware.interface": "Interfaz",
	"about.firmware.valve": "Válvula",
	"about.firmware.second_valve": "Segunda válvula",
	"about.firmware.gateway": "Puerta de enlace",
	"about.firmware.controller": "Controlador",
	"about.firmware.application": "Aplicación",
	"about.firmware.bootloader": "Bootloader",
	"about.firmware.os": "Sistema operativo",
	"about.firmware.certificate": "Certificado",
	"about.firmware.service_pack": "Service pack",
	"about.up_to_date": "Actualizado",
	"about.update_available": "Actualización disponible: {version}",
	"about.update_in_app": "Instala las actualizaciones desde la app Konnect.",
	"about.technical": "Información técnica",
	"about.gateway": "Puerta de enlace",
	"about.valves": "Válvulas",
	"about.interfaces": "Interfaces",
	"about.status": "Estado",
	"about.leak_history": "Historial de fugas",
	"about.no_leaks": "Sin fugas notificadas",
	"about.unknown": "No informado",
	"usage.title": "Consumo de agua",
	"usage.week": "Semana",
	"usage.month": "Mes",
	"usage.year": "Año",
	"usage.average": "Media",
	"usage.high": "Máximo",
	"usage.total": "Total",
	"usage.water": "Agua",
	"usage.time": "Tiempo",
	"usage.per_day": "al día",
	"usage.previous": "Periodo anterior",
	"usage.next": "Periodo siguiente",
	"usage.calendar_week": "Semana natural. Los sensores semanales abarcan los últimos siete días.",
	"usage.error": "Kohler no ha devuelto el consumo de este periodo.",
	"usage.retry": "Reintentar",
	"usage.equivalent_bath": "Bañeras llenas de agua: cerca de {count}",
	"diagnostics.title": "Diagnósticos",
	"diagnostics.stream": "Conexión con Kohler",
	"diagnostics.stream_body": "La conexión en tiempo real de Home Assistant con la nube de Kohler.",
	"diagnostics.cloud": "Dispositivo en línea",
	"diagnostics.cloud_body": "Si la nube de Kohler llega a este dispositivo.",
	"diagnostics.connected": "Conectado",
	"diagnostics.disconnected": "Desconectado",
	"diagnostics.unknown": "Aún sin comprobar",
	"diagnostics.last_update": "Última actualización",
	"diagnostics.faults": "Errores activos",
	"diagnostics.no_faults": "No hay errores activos",
	"diagnostics.clear_errors": "Borrar registro de errores",
	"diagnostics.report_log": "Report Log",
	"diagnostics.report_log_body": "Registra todos los mensajes de Kohler para un informe de errores.",
	"diagnostics.download": "Descargar diagnósticos",
	"diagnostics.download_body": "Un informe de toda la instalación, con las credenciales y los números de serie ocultos.",
	"account.admin_only": "Los ajustes de la cuenta solo están disponibles para administradores.",
	"account.connection": "Conexión con Kohler",
	"account.zone_grouping": "Agrupación de zonas",
	"account.zone_grouping.numbered": "Números de zona",
	"account.zone_grouping.subdevices": "Un subdispositivo por zona",
	"account.zone_grouping.outlet_labels": "Nombres de salidas en los controles",
	"account.safety_limit": "Límite de seguridad del agua",
	"account.safety_limit_body": "Apaga el agua del grifo este tiempo después de que Home Assistant la encienda. 0 desactiva el límite.",
	"account.minutes": "{count} min",
	"account.off": "Apagado",
	"account.sidebar_panel": "Mostrar este panel en la barra lateral",
	"account.sidebar_panel_body": "Al desactivarlo, el panel desaparece para todos los usuarios.",
	"account.save": "Guardar",
	"account.saved": "Guardado",
	"account.reload_warning": "Al guardar, la conexión con Kohler se reinicia durante unos segundos.",
	"dialog.cancel": "Cancelar",
	"dialog.confirm": "Confirmar",
	"control.decrease": "Reducir {name}",
	"control.increase": "Aumentar {name}",
	"generic.controls": "Controles",
	"generic.readings": "Lecturas",
	"generic.press": "Pulsar",
	"generic.press_named": "Pulsar {name}",
	"generic.brightness": "{name}: brillo",
	"generic.effect": "{name}: efecto",
	"generic.untested_body": "Estos controles replican exactamente la app Konnect, pero aún no se han confirmado en hardware real.",
	"generic.ozone_title": "No entres en la ducha",
	"generic.ozone_body": "Una limpieza con ozono hace pasar agua ozonizada por la ducha para limpiarla. Asegúrate de que no haya nadie en la ducha y de que nadie entre hasta que termine.",
	"generic.ozone_confirm": "Iniciar limpieza",
	"unit.minutes_short": "{count} min",
	"unit.hours_minutes": "{hours} h {minutes} min",
	"smart_fan.fan": "Ventilador",
	"smart_fan.light": "Luz",
	"smart_fan.on": "Encendido",
	"smart_fan.fan_on": "Ventilador encendido",
	"smart_fan.light_on": "Luz encendida",
	"smart_fan.fan_favorites": "Favoritos del ventilador",
	"smart_fan.light_favorites": "Favoritos de la luz",
	"smart_fan.stop_all": "Detener todo",
	"smart_fan.locked_running": "No se puede cambiar un ajuste del ventilador o de la luz mientras funciona.",
	"smart_fan.defaults_body": "Lo que hacen el ventilador y la luz al encenderse sin un favorito.",
	"smart_fan.fan_speed": "Velocidad del ventilador",
	"smart_fan.fan_timer": "Temporizador del ventilador",
	"smart_fan.fan_timer_body": "Apaga el ventilador cuando termina su tiempo de funcionamiento.",
	"smart_fan.fan_run_time": "Tiempo de funcionamiento",
	"smart_fan.light_color": "Color de luz",
	"smart_fan.light_brightness": "Brillo de luz",
	"smart_fan.circadian": "Iluminación circadiana",
	"smart_fan.circadian_body": "La luz sigue la hora del día en lugar de un color fijo.",
	"smart_fan.audible_feedback": "Confirmación sonora",
	"smart_fan.audible_feedback_body": "Emite pitidos con las acciones del mando y de la app.",
	"smart_fan.automation": "Automatización del ventilador",
	"smart_fan.humidity": "Detección de humedad",
	"smart_fan.humidity_body": "Enciende el ventilador cuando sube la humedad del aire.",
	"smart_fan.sensitivity": "Sensibilidad",
	"smart_fan.schedule": "Programación del ventilador",
	"smart_fan.schedule_body": "Créala con la acción set_fan_schedule o en la app Konnect; actívala o desactívala aquí.",
	"smart_fan.every_day": "Todos los días",
	"smart_fan.once": "Una vez",
	"toilet.cleaning": "Limpiando",
	"toilet.occupied": "En uso",
	"ice_bath.week_of": "Esta semana: {done} de {target}",
	"ice_bath.week": "Esta semana: {done}"
}, Qe = {
	"app.title": "Kohler Konnect",
	"app.loading": "Connexion à Kohler Konnect…",
	"app.no_devices": "Aucun appareil Kohler pour l'instant",
	"app.no_devices_body": "Les appareils de votre compte Kohler Konnect apparaissent ici dès que l'intégration les a lus.",
	"app.stale_bundle": "Le panneau a été mis à jour. Actualisez pour charger la nouvelle version.",
	"app.refresh": "Actualiser",
	"app.not_found": "Cet appareil ne figure plus sur le compte.",
	"nav.back": "Retour",
	"nav.more": "Plus d'options",
	"nav.menu": "Menu",
	"nav.controls": "Commandes",
	"nav.favorites": "Favoris",
	"nav.experiences": "Expériences",
	"nav.devices": "Appareils",
	"nav.usage": "Consommation",
	"nav.account": "Compte",
	"home.welcome": "Bienvenue, {name}",
	"home.welcome_anonymous": "Bienvenue",
	"home.help": "Besoin d'aide ?",
	"home.summary_quiet": "Tout est calme",
	"home.summary_running": "En marche : {count}",
	"home.summary_alert": "Attention requise : {count}",
	"home.other_area": "Autres",
	"home.usage_this_month": "Ce mois-ci",
	"home.usage_today": "Aujourd'hui",
	"home.usage_last_7_days": "7 derniers jours",
	"status.idle": "Inactif",
	"status.ready": "Prêt",
	"status.off": "Arrêt",
	"status.on": "Marche",
	"status.offline": "Hors ligne",
	"status.running": "En marche",
	"status.warming": "Préchauffage",
	"status.paused": "En pause",
	"status.water_on": "Eau ouverte",
	"status.dispensing": "Distribution",
	"status.leak": "Fuite détectée",
	"status.problem": "Attention requise",
	"status.minutes_left": "{time} restant",
	"status.untested": "Non testé",
	"shower.simple_start": "Démarrage simple",
	"shower.simple_start_unknown": "La vanne n'a pas encore indiqué sa douche par défaut",
	"shower.outlets": "Sorties",
	"shower.zone": "Zone {zone}",
	"shower.temperature": "Température",
	"shower.flow": "Débit",
	"shower.start": "Démarrer",
	"shower.stop": "Arrêter",
	"shower.resume": "Reprendre",
	"shower.cold": "Froid",
	"shower.staged_hint": "Choisissez des sorties, puis appuyez sur Démarrer",
	"shower.locked_warming": "Les modifications sont bloquées pendant le préchauffage de la vanne",
	"shower.locked": "Verrouillé",
	"shower.set_to": "Consigne : {temperature}",
	"shower.at_temperature": "Température atteinte",
	"shower.heating": "Mise à température",
	"shower.warming_title": "Préchauffage",
	"shower.running_title": "L'eau coule",
	"shower.paused_title": "L'eau est prête",
	"shower.paused_body": "Préchauffage terminé. Reprenez pour démarrer votre douche.",
	"shower.time_left": "restant",
	"scenes.empty_favorites": "Aucun favori pour l'instant",
	"scenes.empty_favorites_body": "Les favoris créés dans l'app Konnect ou sur l'écran tactile apparaissent ici.",
	"scenes.empty_experiences": "Aucune expérience pour l'instant",
	"scenes.empty_experiences_body": "Les expériences ajoutées dans l'app Konnect apparaissent ici.",
	"scenes.start": "Démarrer",
	"scenes.running": "En cours",
	"controller.steam": "Vapeur",
	"controller.steam_start": "Démarrer la vapeur",
	"controller.steam_stop": "Arrêter la vapeur",
	"controller.steam_blocked": "La vapeur ne peut pas fonctionner pendant la douche",
	"controller.status": "État",
	"controller.music": "Musique",
	"controller.light": "Lumière",
	"controller.read_only": "Lecture seule : le contrôleur lance des favoris, pas des sorties individuelles.",
	"faucet.faucet": "Robinet",
	"faucet.dispense_amount": "Quantité à distribuer",
	"faucet.start": "Démarrer",
	"faucet.stop": "Arrêter",
	"faucet.see_more": "Voir plus",
	"faucet.see_less": "Voir moins",
	"faucet.presets": "Préréglages",
	"faucet.no_presets": "Les préréglages créés dans l'app Konnect apparaissent ici.",
	"faucet.custom": "Quantité personnalisée",
	"faucet.auto_off": "Coupure automatique dans {time}",
	"faucet.handle_closed": "Ouvrez la manette pour faire couler l'eau à distance.",
	"faucet.firmware_downloading": "Le robinet télécharge un firmware. L'eau ne peut pas être ouverte avant la fin du téléchargement.",
	"faucet.leak": "Kohler a signalé une fuite sur ce robinet.",
	"faucet.clear_leak": "Effacer",
	"faucet.last_dispensed": "Dernière distribution : {amount}",
	"settings.title": "Réglages",
	"settings.locked_running": "Les réglages ne peuvent pas être modifiés pendant que l'eau coule.",
	"settings.defaults": "Réglages par défaut",
	"settings.defaults_body": "Les valeurs définies ici s'appliquent par défaut au démarrage de votre douche.",
	"settings.default_temperature": "Température",
	"settings.default_outlets": "Sorties",
	"settings.default_outlets_body": "Modifiez les sorties par défaut dans l'app Konnect.",
	"settings.device_control": "Contrôle de l'appareil",
	"settings.warmup": "Préchauffage",
	"settings.warmup_body": "Chauffe l'eau avant le début de la douche, puis attend que vous la lanciez.",
	"settings.warmup_outlets": "Sorties à préchauffer",
	"settings.warmup_auto_restore": "Réactivation auto du préchauffage",
	"settings.warmup_auto_restore_body": "Réactive le préchauffage quand quelque chose le désactive sans prévenir.",
	"settings.max_temperature": "Température maximale",
	"settings.max_duration": "Durée maximale de la douche",
	"settings.flow": "Débit",
	"settings.general": "Général",
	"settings.about": "À propos",
	"settings.water_usage": "Consommation d'eau",
	"settings.diagnostics": "Diagnostics",
	"settings.restart": "Redémarrer le produit",
	"settings.save": "Enregistrer",
	"settings.cancel": "Annuler",
	"settings.dispense_defaults": "Distribution",
	"settings.dispense_amount": "Quantité à distribuer",
	"settings.preset": "Préréglage",
	"settings.safety_limit": "Limite de sécurité de l'eau",
	"settings.safety_limit_body": "Se règle pour tout le compte, dans l'onglet Compte.",
	"settings.web_settings": "Réglages web du contrôleur",
	"settings.scald_title": "Modifier la limite anti-brûlure ?",
	"settings.scald_body": "La température maximale est celle de l'eau la plus chaude que cette douche peut délivrer. La relever à {temperature} permet à toutes les sorties d'atteindre cette température.",
	"settings.scald_confirm": "Modifier la limite",
	"settings.restart_title": "Redémarrer {name} ?",
	"settings.restart_body": "La vanne redémarre, comme avec « Redémarrer le produit » dans l'app Konnect. L'eau qui coule s'arrête.",
	"settings.restart_confirm": "Redémarrer",
	"settings.restart_done": "Redémarrage envoyé.",
	"settings.touchscreen": "Écran tactile",
	"settings.touchscreen_body": "Chaque modification est écrite sur tous les panneaux à la fois, comme le fait le Panneau de commande de l'app Konnect.",
	"settings.panel_language": "Langue du panneau",
	"settings.panel_units": "Unités du panneau",
	"settings.wake_screen": "Écran au réveil",
	"settings.display_brightness": "Luminosité de l'écran",
	"settings.haptic_feedback": "Retour haptique",
	"settings.accessible_display": "Affichage accessible",
	"settings.accessible_display_body": "Ajoute des commandes à l'écran qui reproduisent la molette. Les panneaux peuvent mettre quelques instants à se mettre à jour.",
	"settings.flow_control": "Contrôle du débit",
	"settings.flow_control_body": "Permet de régler le débit sur l'écran principal du panneau.",
	"settings.water_saving_mode": "Mode d'économie d'eau",
	"settings.water_saving_mode_body": "Défini à l'installation de la douche : une sortie à la fois. L'app Konnect ne le modifie pas non plus.",
	"about.title": "À propos",
	"about.model": "Modèle",
	"about.serial": "Numéro de série",
	"about.install_date": "Date d'installation",
	"about.registered": "Date d'enregistrement",
	"about.hardware": "Matériel",
	"about.firmware": "Firmware",
	"about.firmware.interface": "Interface",
	"about.firmware.valve": "Vanne",
	"about.firmware.second_valve": "Deuxième vanne",
	"about.firmware.gateway": "Passerelle",
	"about.firmware.controller": "Contrôleur",
	"about.firmware.application": "Application",
	"about.firmware.bootloader": "Bootloader",
	"about.firmware.os": "Système d'exploitation",
	"about.firmware.certificate": "Certificat",
	"about.firmware.service_pack": "Service pack",
	"about.up_to_date": "À jour",
	"about.update_available": "Mise à jour disponible : {version}",
	"about.update_in_app": "Installez les mises à jour depuis l'app Konnect.",
	"about.technical": "Informations techniques",
	"about.gateway": "Passerelle",
	"about.valves": "Vannes",
	"about.interfaces": "Interfaces",
	"about.status": "État",
	"about.leak_history": "Historique des fuites",
	"about.no_leaks": "Aucune fuite signalée",
	"about.unknown": "Non communiqué",
	"usage.title": "Consommation d'eau",
	"usage.week": "Semaine",
	"usage.month": "Mois",
	"usage.year": "Année",
	"usage.average": "Moyenne",
	"usage.high": "Maximum",
	"usage.total": "Total",
	"usage.water": "Eau",
	"usage.time": "Durée",
	"usage.per_day": "par jour",
	"usage.previous": "Période précédente",
	"usage.next": "Période suivante",
	"usage.calendar_week": "Semaine civile. Le capteur « cette semaine » couvre les sept derniers jours glissants.",
	"usage.error": "Kohler n'a renvoyé aucune donnée de consommation pour cette période.",
	"usage.retry": "Réessayer",
	"usage.equivalent_bath": "Baignoires pleines : environ {count}",
	"diagnostics.title": "Diagnostics",
	"diagnostics.stream": "Connexion à Kohler",
	"diagnostics.stream_body": "Le lien en direct entre Home Assistant et le cloud Kohler.",
	"diagnostics.cloud": "Appareil en ligne",
	"diagnostics.cloud_body": "Indique si le cloud Kohler peut joindre cet appareil.",
	"diagnostics.connected": "Connecté",
	"diagnostics.disconnected": "Déconnecté",
	"diagnostics.unknown": "Pas encore vérifié",
	"diagnostics.last_update": "Dernière mise à jour",
	"diagnostics.faults": "Défauts actifs",
	"diagnostics.no_faults": "Aucun défaut actif",
	"diagnostics.clear_errors": "Effacer le journal d'erreurs",
	"diagnostics.report_log": "Report Log",
	"diagnostics.report_log_body": "Enregistre chaque message Kohler pour un rapport de bug.",
	"diagnostics.download": "Télécharger les diagnostics",
	"diagnostics.download_body": "Un rapport unique de toute l'installation, avec identifiants et numéros de série masqués.",
	"account.admin_only": "Les réglages du compte sont réservés aux administrateurs.",
	"account.connection": "Connexion à Kohler",
	"account.zone_grouping": "Regroupement des zones",
	"account.zone_grouping.numbered": "Numéros de zone",
	"account.zone_grouping.subdevices": "Un sous-appareil par zone",
	"account.zone_grouping.outlet_labels": "Noms des sorties sur les commandes",
	"account.safety_limit": "Limite de sécurité de l'eau",
	"account.safety_limit_body": "Coupe l'eau du robinet après cette durée quand Home Assistant l'a ouverte. 0 désactive la limite.",
	"account.minutes": "{count} min",
	"account.off": "Désactivée",
	"account.sidebar_panel": "Afficher ce panneau dans la barre latérale",
	"account.sidebar_panel_body": "Le désactiver retire le panneau pour tous les utilisateurs.",
	"account.save": "Enregistrer",
	"account.saved": "Enregistré",
	"account.reload_warning": "L'enregistrement entraîne une reconnexion à Kohler de quelques secondes.",
	"dialog.cancel": "Annuler",
	"dialog.confirm": "Confirmer",
	"control.decrease": "Diminuer {name}",
	"control.increase": "Augmenter {name}",
	"generic.controls": "Commandes",
	"generic.readings": "Relevés",
	"generic.press": "Appuyer",
	"generic.press_named": "Appuyer sur {name}",
	"generic.brightness": "{name} : luminosité",
	"generic.effect": "{name} : effet",
	"generic.untested_body": "Ces commandes reproduisent exactement l'app Konnect, mais n'ont pas encore été confirmées sur du matériel réel.",
	"generic.ozone_title": "Ne restez pas dans la douche",
	"generic.ozone_body": "Un nettoyage à l'ozone fait circuler de l'eau ozonée dans la douche pour la nettoyer. Vérifiez que personne n'est dans la douche et que personne n'y entre avant la fin du nettoyage.",
	"generic.ozone_confirm": "Lancer le nettoyage",
	"unit.minutes_short": "{count} min",
	"unit.hours_minutes": "{hours} h {minutes} min",
	"smart_fan.fan": "Ventilateur",
	"smart_fan.light": "Lumière",
	"smart_fan.on": "Marche",
	"smart_fan.fan_on": "Ventilateur en marche",
	"smart_fan.light_on": "Lumière allumée",
	"smart_fan.fan_favorites": "Favoris du ventilateur",
	"smart_fan.light_favorites": "Favoris de la lumière",
	"smart_fan.stop_all": "Tout arrêter",
	"smart_fan.locked_running": "Un réglage du ventilateur ou de la lumière ne peut pas être modifié pendant son fonctionnement.",
	"smart_fan.defaults_body": "Ce que font le ventilateur et la lumière lorsqu'ils démarrent sans favori.",
	"smart_fan.fan_speed": "Vitesse du ventilateur",
	"smart_fan.fan_timer": "Minuterie du ventilateur",
	"smart_fan.fan_timer_body": "Arrête le ventilateur après sa durée de fonctionnement.",
	"smart_fan.fan_run_time": "Durée de fonctionnement",
	"smart_fan.light_color": "Couleur de lumière",
	"smart_fan.light_brightness": "Luminosité",
	"smart_fan.circadian": "Éclairage circadien",
	"smart_fan.circadian_body": "La lumière suit le moment de la journée au lieu d'une couleur fixe.",
	"smart_fan.audible_feedback": "Retour sonore",
	"smart_fan.audible_feedback_body": "Émet un bip lors des actions sur la télécommande ou dans l'app.",
	"smart_fan.automation": "Automatisation du ventilateur",
	"smart_fan.humidity": "Détection d'humidité",
	"smart_fan.humidity_body": "Démarre le ventilateur quand l'air devient humide.",
	"smart_fan.sensitivity": "Sensibilité",
	"smart_fan.schedule": "Programmation du ventilateur",
	"smart_fan.schedule_body": "Créez-en une avec l'action set_fan_schedule ou dans l'app Konnect ; activez-la ou désactivez-la ici.",
	"smart_fan.every_day": "Tous les jours",
	"smart_fan.once": "Une fois",
	"toilet.cleaning": "Nettoyage",
	"toilet.occupied": "Occupé",
	"ice_bath.week_of": "Cette semaine : {done} sur {target}",
	"ice_bath.week": "Cette semaine : {done}"
}, $e = {
	"app.title": "Kohler Konnect",
	"app.loading": "Connessione a Kohler Konnect…",
	"app.no_devices": "Ancora nessun dispositivo Kohler",
	"app.no_devices_body": "I dispositivi del tuo account Kohler Konnect compaiono qui dopo che l'integrazione li ha letti.",
	"app.stale_bundle": "Il pannello è stato aggiornato. Aggiorna la pagina per caricare la nuova versione.",
	"app.refresh": "Aggiorna",
	"app.not_found": "Questo dispositivo non è più nell'account.",
	"nav.back": "Indietro",
	"nav.more": "Altro",
	"nav.menu": "Menu",
	"nav.controls": "Controlli",
	"nav.favorites": "Preferiti",
	"nav.experiences": "Esperienze",
	"nav.devices": "Dispositivi",
	"nav.usage": "Consumi",
	"nav.account": "Account",
	"home.welcome": "Ciao, {name}",
	"home.welcome_anonymous": "Ciao",
	"home.help": "Serve aiuto?",
	"home.summary_quiet": "Tutto tranquillo",
	"home.summary_running": "In funzione: {count}",
	"home.summary_alert": "Da controllare: {count}",
	"home.other_area": "Altro",
	"home.usage_this_month": "Questo mese",
	"home.usage_today": "Oggi",
	"home.usage_last_7_days": "Ultimi 7 giorni",
	"status.idle": "Inattivo",
	"status.ready": "Pronto",
	"status.off": "Spento",
	"status.on": "Acceso",
	"status.offline": "Offline",
	"status.running": "In funzione",
	"status.warming": "Preriscaldamento",
	"status.paused": "In pausa",
	"status.water_on": "Acqua aperta",
	"status.dispensing": "Erogazione",
	"status.leak": "Perdita rilevata",
	"status.problem": "Richiede attenzione",
	"status.minutes_left": "{time} rimanenti",
	"status.untested": "Non testato",
	"shower.simple_start": "Avvio semplice",
	"shower.simple_start_unknown": "La valvola non ha ancora comunicato la doccia predefinita",
	"shower.outlets": "Uscite",
	"shower.zone": "Zona {zone}",
	"shower.temperature": "Temperatura",
	"shower.flow": "Portata",
	"shower.start": "Avvia",
	"shower.stop": "Ferma",
	"shower.resume": "Riprendi",
	"shower.cold": "Fredda",
	"shower.staged_hint": "Scegli le uscite, poi premi Avvia",
	"shower.locked_warming": "Le modifiche sono bloccate durante il preriscaldamento della valvola",
	"shower.locked": "Bloccato",
	"shower.set_to": "Impostata a {temperature}",
	"shower.at_temperature": "Temperatura raggiunta",
	"shower.heating": "Raggiungimento temperatura",
	"shower.warming_title": "Preriscaldamento",
	"shower.running_title": "L'acqua scorre",
	"shower.paused_title": "Pronta quando vuoi",
	"shower.paused_body": "Preriscaldamento terminato. Riprendi per avviare la doccia.",
	"shower.time_left": "rimanenti",
	"scenes.empty_favorites": "Ancora nessun preferito",
	"scenes.empty_favorites_body": "I preferiti creati nell'app Konnect o sul touchscreen compaiono qui.",
	"scenes.empty_experiences": "Ancora nessuna esperienza",
	"scenes.empty_experiences_body": "Le esperienze aggiunte nell'app Konnect compaiono qui.",
	"scenes.start": "Avvia",
	"scenes.running": "In corso",
	"controller.steam": "Vapore",
	"controller.steam_start": "Avvia vapore",
	"controller.steam_stop": "Ferma vapore",
	"controller.steam_blocked": "Il vapore non può partire mentre la doccia è in funzione",
	"controller.status": "Stato",
	"controller.music": "Musica",
	"controller.light": "Luce",
	"controller.read_only": "Sola lettura: il controller avvia i preferiti, non le singole uscite.",
	"faucet.faucet": "Rubinetto",
	"faucet.dispense_amount": "Quantità da erogare",
	"faucet.start": "Avvia",
	"faucet.stop": "Ferma",
	"faucet.see_more": "Mostra di più",
	"faucet.see_less": "Mostra di meno",
	"faucet.presets": "Preimpostazioni",
	"faucet.no_presets": "Le preimpostazioni create nell'app Konnect compaiono qui.",
	"faucet.custom": "Quantità personalizzata",
	"faucet.auto_off": "Chiusura automatica tra {time}",
	"faucet.handle_closed": "Apri la leva per far scorrere l'acqua da remoto.",
	"faucet.firmware_downloading": "Il rubinetto sta scaricando il firmware. Non è possibile aprire l'acqua finché il download non termina.",
	"faucet.leak": "Kohler ha segnalato una perdita da questo rubinetto.",
	"faucet.clear_leak": "Cancella",
	"faucet.last_dispensed": "Ultima erogazione: {amount}",
	"settings.title": "Impostazioni",
	"settings.locked_running": "Le impostazioni non si possono modificare mentre l'acqua scorre.",
	"settings.defaults": "Impostazioni predefinite",
	"settings.defaults_body": "I valori impostati qui vengono usati come predefiniti quando avvii la doccia.",
	"settings.default_temperature": "Temperatura",
	"settings.default_outlets": "Uscite",
	"settings.default_outlets_body": "Modifica le uscite predefinite nell'app Konnect.",
	"settings.device_control": "Controllo del dispositivo",
	"settings.warmup": "Preriscaldamento",
	"settings.warmup_body": "Riscalda l'acqua prima che inizi la doccia, poi resta in attesa finché non la avvii.",
	"settings.warmup_outlets": "Uscite da preriscaldare",
	"settings.warmup_auto_restore": "Autoripristino preriscaldamento",
	"settings.warmup_auto_restore_body": "Riattiva il preriscaldamento quando qualcosa lo disattiva senza avvisare.",
	"settings.max_temperature": "Temperatura massima",
	"settings.max_duration": "Durata massima della doccia",
	"settings.flow": "Portata",
	"settings.general": "Generale",
	"settings.about": "Informazioni",
	"settings.water_usage": "Consumo d'acqua",
	"settings.diagnostics": "Diagnostica",
	"settings.restart": "Riavvia il prodotto",
	"settings.save": "Salva",
	"settings.cancel": "Annulla",
	"settings.dispense_defaults": "Erogazione",
	"settings.dispense_amount": "Quantità da erogare",
	"settings.preset": "Preimpostazione",
	"settings.safety_limit": "Limite di sicurezza dell'acqua",
	"settings.safety_limit_body": "Si imposta per tutto l'account, nella scheda Account.",
	"settings.web_settings": "Impostazioni web del controller",
	"settings.scald_title": "Modificare il limite antiscottatura?",
	"settings.scald_body": "La temperatura massima è quella dell'acqua più calda che questa doccia può erogare. Alzarla a {temperature} permette a tutte le uscite di raggiungere quella temperatura.",
	"settings.scald_confirm": "Modifica limite",
	"settings.restart_title": "Riavviare {name}?",
	"settings.restart_body": "La valvola si riavvia, come con «Riavvia il prodotto» nell'app Konnect. L'acqua che scorre si ferma.",
	"settings.restart_confirm": "Riavvia",
	"settings.restart_done": "Riavvio inviato.",
	"settings.touchscreen": "Touchscreen",
	"settings.touchscreen_body": "Ogni modifica viene scritta su tutti i pannelli insieme, come fa il Pannello di controllo dell'app Konnect.",
	"settings.panel_language": "Lingua del pannello",
	"settings.panel_units": "Unità del pannello",
	"settings.wake_screen": "Schermata al risveglio",
	"settings.display_brightness": "Luminosità del display",
	"settings.haptic_feedback": "Feedback aptico",
	"settings.accessible_display": "Display accessibile",
	"settings.accessible_display_body": "Aggiunge comandi sullo schermo che replicano la manopola. I pannelli potrebbero impiegare qualche istante ad aggiornarsi.",
	"settings.flow_control": "Controllo del flusso",
	"settings.flow_control_body": "Permette di regolare il flusso sulla schermata principale del pannello.",
	"settings.water_saving_mode": "Modalità risparmio acqua",
	"settings.water_saving_mode_body": "Impostata all'installazione della doccia: un'uscita alla volta. Nemmeno l'app Konnect la cambia.",
	"about.title": "Informazioni",
	"about.model": "Modello",
	"about.serial": "Numero di serie",
	"about.install_date": "Data di installazione",
	"about.registered": "Data di registrazione",
	"about.hardware": "Hardware",
	"about.firmware": "Firmware",
	"about.firmware.interface": "Interfaccia",
	"about.firmware.valve": "Valvola",
	"about.firmware.second_valve": "Seconda valvola",
	"about.firmware.gateway": "Gateway",
	"about.firmware.controller": "Controller",
	"about.firmware.application": "Applicazione",
	"about.firmware.bootloader": "Bootloader",
	"about.firmware.os": "Sistema operativo",
	"about.firmware.certificate": "Certificato",
	"about.firmware.service_pack": "Service pack",
	"about.up_to_date": "Aggiornato",
	"about.update_available": "Aggiornamento disponibile: {version}",
	"about.update_in_app": "Installa gli aggiornamenti dall'app Konnect.",
	"about.technical": "Informazioni tecniche",
	"about.gateway": "Gateway",
	"about.valves": "Valvole",
	"about.interfaces": "Interfacce",
	"about.status": "Stato",
	"about.leak_history": "Cronologia delle perdite",
	"about.no_leaks": "Nessuna perdita segnalata",
	"about.unknown": "Non comunicato",
	"usage.title": "Consumo d'acqua",
	"usage.week": "Settimana",
	"usage.month": "Mese",
	"usage.year": "Anno",
	"usage.average": "Media",
	"usage.high": "Massimo",
	"usage.total": "Totale",
	"usage.water": "Acqua",
	"usage.time": "Durata",
	"usage.per_day": "al giorno",
	"usage.previous": "Periodo precedente",
	"usage.next": "Periodo successivo",
	"usage.calendar_week": "Settimana solare. Il sensore «questa settimana» copre gli ultimi sette giorni.",
	"usage.error": "Kohler non ha restituito i consumi per questo periodo.",
	"usage.retry": "Riprova",
	"usage.equivalent_bath": "Vasche da bagno piene: circa {count}",
	"diagnostics.title": "Diagnostica",
	"diagnostics.stream": "Connessione a Kohler",
	"diagnostics.stream_body": "Il collegamento in tempo reale tra Home Assistant e il cloud Kohler.",
	"diagnostics.cloud": "Dispositivo online",
	"diagnostics.cloud_body": "Indica se il cloud Kohler riesce a raggiungere questo dispositivo.",
	"diagnostics.connected": "Connesso",
	"diagnostics.disconnected": "Disconnesso",
	"diagnostics.unknown": "Non ancora verificato",
	"diagnostics.last_update": "Ultimo aggiornamento",
	"diagnostics.faults": "Guasti attivi",
	"diagnostics.no_faults": "Nessun guasto attivo",
	"diagnostics.clear_errors": "Cancella il registro errori",
	"diagnostics.report_log": "Report Log",
	"diagnostics.report_log_body": "Registra ogni messaggio Kohler per una segnalazione di bug.",
	"diagnostics.download": "Scarica la diagnostica",
	"diagnostics.download_body": "Un unico report dell'intera installazione, con credenziali e numeri di serie oscurati.",
	"account.admin_only": "Le impostazioni dell'account sono riservate agli amministratori.",
	"account.connection": "Connessione a Kohler",
	"account.zone_grouping": "Raggruppamento delle zone",
	"account.zone_grouping.numbered": "Numeri di zona",
	"account.zone_grouping.subdevices": "Un sottodispositivo per zona",
	"account.zone_grouping.outlet_labels": "Nomi delle uscite sui controlli",
	"account.safety_limit": "Limite di sicurezza dell'acqua",
	"account.safety_limit_body": "Chiude l'acqua del rubinetto dopo questo tempo, se è stata aperta da Home Assistant. 0 disattiva il limite.",
	"account.minutes": "{count} min",
	"account.off": "Disattivato",
	"account.sidebar_panel": "Mostra questo pannello nella barra laterale",
	"account.sidebar_panel_body": "Se lo disattivi, il pannello scompare per tutti gli utenti.",
	"account.save": "Salva",
	"account.saved": "Salvato",
	"account.reload_warning": "Il salvataggio comporta una riconnessione a Kohler di qualche secondo.",
	"dialog.cancel": "Annulla",
	"dialog.confirm": "Conferma",
	"control.decrease": "Diminuisci {name}",
	"control.increase": "Aumenta {name}",
	"generic.controls": "Controlli",
	"generic.readings": "Letture",
	"generic.press": "Premi",
	"generic.press_named": "Premi {name}",
	"generic.brightness": "{name}: luminosità",
	"generic.effect": "{name}: effetto",
	"generic.untested_body": "Questi controlli seguono esattamente l'app Konnect, ma non sono ancora stati verificati su hardware reale.",
	"generic.ozone_title": "Non entrare nella doccia",
	"generic.ozone_body": "Una pulizia all'ozono fa passare acqua ozonizzata nella doccia per pulirla. Assicurati che nessuno sia nella doccia e che nessuno entri finché la pulizia non è terminata.",
	"generic.ozone_confirm": "Avvia pulizia",
	"unit.minutes_short": "{count} min",
	"unit.hours_minutes": "{hours} h {minutes} min",
	"smart_fan.fan": "Ventola",
	"smart_fan.light": "Luce",
	"smart_fan.on": "Accesa",
	"smart_fan.fan_on": "Ventola accesa",
	"smart_fan.light_on": "Luce accesa",
	"smart_fan.fan_favorites": "Preferiti della ventola",
	"smart_fan.light_favorites": "Preferiti della luce",
	"smart_fan.stop_all": "Ferma tutto",
	"smart_fan.locked_running": "Un'impostazione della ventola o della luce non si può modificare mentre è accesa.",
	"smart_fan.defaults_body": "Cosa fanno la ventola e la luce quando si avviano senza un preferito.",
	"smart_fan.fan_speed": "Velocità della ventola",
	"smart_fan.fan_timer": "Timer della ventola",
	"smart_fan.fan_timer_body": "Spegne la ventola al termine della durata di funzionamento.",
	"smart_fan.fan_run_time": "Durata di funzionamento",
	"smart_fan.light_color": "Colore della luce",
	"smart_fan.light_brightness": "Luminosità della luce",
	"smart_fan.circadian": "Illuminazione circadiana",
	"smart_fan.circadian_body": "La luce segue l'ora del giorno invece di un colore fisso.",
	"smart_fan.audible_feedback": "Segnale acustico",
	"smart_fan.audible_feedback_body": "Emette un bip per le azioni dal telecomando e dall'app.",
	"smart_fan.automation": "Automazione della ventola",
	"smart_fan.humidity": "Rilevamento dell'umidità",
	"smart_fan.humidity_body": "Avvia la ventola quando l'aria diventa umida.",
	"smart_fan.sensitivity": "Sensibilità",
	"smart_fan.schedule": "Programmazione della ventola",
	"smart_fan.schedule_body": "Creane una con l'azione set_fan_schedule o nell'app Konnect; attivala o disattivala qui.",
	"smart_fan.every_day": "Ogni giorno",
	"smart_fan.once": "Una volta",
	"toilet.cleaning": "Pulizia",
	"toilet.occupied": "Occupato",
	"ice_bath.week_of": "Questa settimana: {done} su {target}",
	"ice_bath.week": "Questa settimana: {done}"
}, et = {
	"app.title": "Kohler Konnect",
	"app.loading": "Verbinding maken met Kohler Konnect…",
	"app.no_devices": "Nog geen Kohler-apparaten",
	"app.no_devices_body": "Apparaten in je Kohler Konnect-account verschijnen hier zodra de integratie ze heeft uitgelezen.",
	"app.stale_bundle": "Het paneel is bijgewerkt. Laad de pagina opnieuw voor de nieuwe versie.",
	"app.refresh": "Opnieuw laden",
	"app.not_found": "Dit apparaat staat niet meer in het account.",
	"nav.back": "Terug",
	"nav.more": "Meer",
	"nav.menu": "Menu",
	"nav.controls": "Bediening",
	"nav.favorites": "Favorieten",
	"nav.experiences": "Belevingen",
	"nav.devices": "Apparaten",
	"nav.usage": "Verbruik",
	"nav.account": "Account",
	"home.welcome": "Welkom, {name}",
	"home.welcome_anonymous": "Welkom",
	"home.help": "Hulp nodig?",
	"home.summary_quiet": "Alles rustig",
	"home.summary_running": "Actief: {count}",
	"home.summary_alert": "Aandacht nodig: {count}",
	"home.other_area": "Overig",
	"home.usage_this_month": "Deze maand",
	"home.usage_today": "Vandaag",
	"home.usage_last_7_days": "Afgelopen 7 dagen",
	"status.idle": "Inactief",
	"status.ready": "Gereed",
	"status.off": "Uit",
	"status.on": "Aan",
	"status.offline": "Offline",
	"status.running": "Actief",
	"status.warming": "Voorverwarmen",
	"status.paused": "Gepauzeerd",
	"status.water_on": "Water aan",
	"status.dispensing": "Doseren",
	"status.leak": "Lek gedetecteerd",
	"status.problem": "Aandacht nodig",
	"status.minutes_left": "Nog {time}",
	"status.untested": "Ongetest",
	"shower.simple_start": "Snelstart",
	"shower.simple_start_unknown": "De klep heeft de standaarddouche nog niet gemeld",
	"shower.outlets": "Uitgangen",
	"shower.zone": "Zone {zone}",
	"shower.temperature": "Temperatuur",
	"shower.flow": "Doorstroming",
	"shower.start": "Starten",
	"shower.stop": "Stoppen",
	"shower.resume": "Hervatten",
	"shower.cold": "Koud",
	"shower.staged_hint": "Kies uitgangen en druk dan op Starten",
	"shower.locked_warming": "Wijzigingen zijn vergrendeld terwijl de klep voorverwarmt",
	"shower.locked": "Vergrendeld",
	"shower.set_to": "Ingesteld op {temperature}",
	"shower.at_temperature": "Op temperatuur",
	"shower.heating": "Warmt op",
	"shower.warming_title": "Voorverwarmen",
	"shower.running_title": "Water loopt",
	"shower.paused_title": "Klaar wanneer jij wilt",
	"shower.paused_body": "Voorverwarmen voltooid. Druk op Hervatten om je douche te starten.",
	"shower.time_left": "resterend",
	"scenes.empty_favorites": "Nog geen favorieten",
	"scenes.empty_favorites_body": "Favorieten die je in de Konnect-app of op het touchscreen maakt, verschijnen hier.",
	"scenes.empty_experiences": "Nog geen belevingen",
	"scenes.empty_experiences_body": "Belevingen die je in de Konnect-app toevoegt, verschijnen hier.",
	"scenes.start": "Starten",
	"scenes.running": "Actief",
	"controller.steam": "Stoom",
	"controller.steam_start": "Stoom starten",
	"controller.steam_stop": "Stoom stoppen",
	"controller.steam_blocked": "Stoom kan niet aan zolang de douche loopt",
	"controller.status": "Status",
	"controller.music": "Muziek",
	"controller.light": "Licht",
	"controller.read_only": "Alleen-lezen: de controller start favorieten, geen afzonderlijke uitgangen.",
	"faucet.faucet": "Kraan",
	"faucet.dispense_amount": "Hoeveelheid doseren",
	"faucet.start": "Starten",
	"faucet.stop": "Stoppen",
	"faucet.see_more": "Meer tonen",
	"faucet.see_less": "Minder tonen",
	"faucet.presets": "Voorinstellingen",
	"faucet.no_presets": "Voorinstellingen die je in de Konnect-app maakt, verschijnen hier.",
	"faucet.custom": "Eigen hoeveelheid",
	"faucet.auto_off": "Gaat over {time} automatisch uit",
	"faucet.handle_closed": "Open de hendel om het water op afstand te laten lopen.",
	"faucet.firmware_downloading": "De kraan downloadt firmware. Het water kan pas worden aangezet als dat klaar is.",
	"faucet.leak": "Kohler heeft een lek bij deze kraan gemeld.",
	"faucet.clear_leak": "Wissen",
	"faucet.last_dispensed": "Laatst {amount} gedoseerd",
	"settings.title": "Instellingen",
	"settings.locked_running": "Instellingen kunnen niet worden gewijzigd zolang er water loopt.",
	"settings.defaults": "Standaardinstellingen",
	"settings.defaults_body": "Deze waarden worden standaard gebruikt als je je douche start.",
	"settings.default_temperature": "Temperatuur",
	"settings.default_outlets": "Uitgangen",
	"settings.default_outlets_body": "Wijzig de standaarduitgangen in de Konnect-app.",
	"settings.device_control": "Apparaatbediening",
	"settings.warmup": "Douche voorverwarmen",
	"settings.warmup_body": "Verwarmt het water voordat de douche start en wacht dan tot je klaar bent.",
	"settings.warmup_outlets": "Uitgangen om voor te verwarmen",
	"settings.warmup_auto_restore": "Voorverwarmen herstellen",
	"settings.warmup_auto_restore_body": "Zet voorverwarmen weer aan als iets het ongemerkt uitzet.",
	"settings.max_temperature": "Maximale temperatuur",
	"settings.max_duration": "Maximale doucheduur",
	"settings.flow": "Doorstroming",
	"settings.general": "Algemeen",
	"settings.about": "Info",
	"settings.water_usage": "Waterverbruik",
	"settings.diagnostics": "Diagnose",
	"settings.restart": "Product herstarten",
	"settings.save": "Opslaan",
	"settings.cancel": "Annuleren",
	"settings.dispense_defaults": "Doseren",
	"settings.dispense_amount": "Doseerhoeveelheid",
	"settings.preset": "Voorinstelling",
	"settings.safety_limit": "Waterveiligheidslimiet",
	"settings.safety_limit_body": "Geldt voor het hele account en wordt ingesteld onder Account.",
	"settings.web_settings": "Webinstellingen van de controller",
	"settings.scald_title": "Verbrandingsbeveiliging wijzigen?",
	"settings.scald_body": "De maximale temperatuur bepaalt hoe heet het water van deze douche kan worden. Verhoog je die naar {temperature}, dan kan elke uitgang zo heet water geven.",
	"settings.scald_confirm": "Limiet wijzigen",
	"settings.restart_title": "{name} herstarten?",
	"settings.restart_body": "De klep start opnieuw op, net als met ‘Product herstarten’ in de Konnect-app. Lopend water stopt.",
	"settings.restart_confirm": "Herstarten",
	"settings.restart_done": "Herstart verzonden.",
	"settings.touchscreen": "Touchscreen",
	"settings.touchscreen_body": "Elke wijziging wordt naar alle panelen tegelijk geschreven, zoals het bedieningspaneel van de Konnect-app doet.",
	"settings.panel_language": "Paneeltaal",
	"settings.panel_units": "Paneeleenheden",
	"settings.wake_screen": "Wekscherm",
	"settings.display_brightness": "Schermhelderheid",
	"settings.haptic_feedback": "Haptische feedback",
	"settings.accessible_display": "Toegankelijk scherm",
	"settings.accessible_display_body": "Voegt knoppen op het scherm toe die de draaiknop nabootsen. De panelen kunnen even nodig hebben om bij te werken.",
	"settings.flow_control": "Debietregeling",
	"settings.flow_control_body": "Hiermee kan het debiet op het hoofdscherm van het paneel worden ingesteld.",
	"settings.water_saving_mode": "Waterbesparende modus",
	"settings.water_saving_mode_body": "Ingesteld bij de installatie van de douche: één uitlaat tegelijk. Ook de Konnect-app verandert dit niet.",
	"about.title": "Info",
	"about.model": "Model",
	"about.serial": "Serienummer",
	"about.install_date": "Installatiedatum",
	"about.registered": "Geregistreerd",
	"about.hardware": "Hardware",
	"about.firmware": "Firmware",
	"about.firmware.interface": "Bedieningspaneel",
	"about.firmware.valve": "Klep",
	"about.firmware.second_valve": "Tweede klep",
	"about.firmware.gateway": "Gateway",
	"about.firmware.controller": "Controller",
	"about.firmware.application": "Applicatie",
	"about.firmware.bootloader": "Bootloader",
	"about.firmware.os": "Besturingssysteem",
	"about.firmware.certificate": "Certificaat",
	"about.firmware.service_pack": "Servicepack",
	"about.up_to_date": "Up-to-date",
	"about.update_available": "Update beschikbaar: {version}",
	"about.update_in_app": "Installeer updates via de Konnect-app.",
	"about.technical": "Technische informatie",
	"about.gateway": "Gateway",
	"about.valves": "Kleppen",
	"about.interfaces": "Bedieningspanelen",
	"about.status": "Status",
	"about.leak_history": "Lekgeschiedenis",
	"about.no_leaks": "Geen lekken gemeld",
	"about.unknown": "Niet gemeld",
	"usage.title": "Waterverbruik",
	"usage.week": "Week",
	"usage.month": "Maand",
	"usage.year": "Jaar",
	"usage.average": "Gemiddeld",
	"usage.high": "Hoogste",
	"usage.total": "Totaal",
	"usage.water": "Water",
	"usage.time": "Duur",
	"usage.per_day": "per dag",
	"usage.previous": "Vorige periode",
	"usage.next": "Volgende periode",
	"usage.calendar_week": "Kalenderweek. De sensor ‘deze week’ telt altijd de afgelopen zeven dagen.",
	"usage.error": "Kohler heeft voor deze periode geen verbruik teruggegeven.",
	"usage.retry": "Probeer opnieuw",
	"usage.equivalent_bath": "Ongeveer {count} keer een vol bad",
	"diagnostics.title": "Diagnose",
	"diagnostics.stream": "Kohler-verbinding",
	"diagnostics.stream_body": "De live-verbinding van Home Assistant met de Kohler-cloud.",
	"diagnostics.cloud": "Apparaat online",
	"diagnostics.cloud_body": "Of de Kohler-cloud dit apparaat kan bereiken.",
	"diagnostics.connected": "Verbonden",
	"diagnostics.disconnected": "Niet verbonden",
	"diagnostics.unknown": "Nog niet gecontroleerd",
	"diagnostics.last_update": "Laatste update",
	"diagnostics.faults": "Actieve storingen",
	"diagnostics.no_faults": "Geen actieve storingen",
	"diagnostics.clear_errors": "Foutenlogboek wissen",
	"diagnostics.report_log": "Report Log",
	"diagnostics.report_log_body": "Legt elk bericht van Kohler vast voor een bugmelding.",
	"diagnostics.download": "Diagnostiek downloaden",
	"diagnostics.download_body": "Eén rapport over de hele installatie, met inloggegevens en serienummers afgeschermd.",
	"account.admin_only": "Accountinstellingen zijn alleen beschikbaar voor beheerders.",
	"account.connection": "Kohler-verbinding",
	"account.zone_grouping": "Zonegroepering",
	"account.zone_grouping.numbered": "Zonenummers",
	"account.zone_grouping.subdevices": "Subapparaat per zone",
	"account.zone_grouping.outlet_labels": "Uitgangsnamen bij de bediening",
	"account.safety_limit": "Waterveiligheidslimiet",
	"account.safety_limit_body": "Zet water dat Home Assistant bij een kraan heeft aangezet na deze tijd weer uit. 0 schakelt de limiet uit.",
	"account.minutes": "{count} min",
	"account.off": "Uit",
	"account.sidebar_panel": "Dit paneel in de zijbalk tonen",
	"account.sidebar_panel_body": "Uitschakelen verwijdert het paneel voor alle gebruikers.",
	"account.save": "Opslaan",
	"account.saved": "Opgeslagen",
	"account.reload_warning": "Bij het opslaan wordt de verbinding met Kohler een paar seconden opnieuw opgebouwd.",
	"dialog.cancel": "Annuleren",
	"dialog.confirm": "Bevestigen",
	"control.decrease": "{name} verlagen",
	"control.increase": "{name} verhogen",
	"generic.controls": "Bediening",
	"generic.readings": "Meetwaarden",
	"generic.press": "Indrukken",
	"generic.press_named": "{name} indrukken",
	"generic.brightness": "{name}: helderheid",
	"generic.effect": "{name}: effect",
	"generic.untested_body": "Deze bedieningselementen volgen de Konnect-app precies, maar zijn nog niet bevestigd op echte hardware.",
	"generic.ozone_title": "Blijf uit de douche",
	"generic.ozone_body": "Een ozonreiniging stuurt ozonhoudend water door de douche om die te reinigen. Zorg dat er niemand in de douche staat en dat niemand erin gaat tot de reiniging klaar is.",
	"generic.ozone_confirm": "Reiniging starten",
	"unit.minutes_short": "{count} min",
	"unit.hours_minutes": "{hours} h {minutes} min",
	"smart_fan.fan": "Ventilator",
	"smart_fan.light": "Licht",
	"smart_fan.on": "Aan",
	"smart_fan.fan_on": "Ventilator aan",
	"smart_fan.light_on": "Licht aan",
	"smart_fan.fan_favorites": "Ventilatorfavorieten",
	"smart_fan.light_favorites": "Lichtfavorieten",
	"smart_fan.stop_all": "Alles stoppen",
	"smart_fan.locked_running": "Instellingen voor ventilator en licht kunnen niet worden gewijzigd zolang ze aan staan.",
	"smart_fan.defaults_body": "Wat de ventilator en het licht doen als ze zonder favoriet worden gestart.",
	"smart_fan.fan_speed": "Ventilatorsnelheid",
	"smart_fan.fan_timer": "Ventilatortimer",
	"smart_fan.fan_timer_body": "Zet de ventilator uit als de looptijd voorbij is.",
	"smart_fan.fan_run_time": "Looptijd ventilator",
	"smart_fan.light_color": "Lichtkleur",
	"smart_fan.light_brightness": "Lichthelderheid",
	"smart_fan.circadian": "Circadiaanse verlichting",
	"smart_fan.circadian_body": "Het licht volgt het tijdstip van de dag in plaats van een vaste kleur.",
	"smart_fan.audible_feedback": "Geluidssignaal",
	"smart_fan.audible_feedback_body": "Piept bij acties via de afstandsbediening en de app.",
	"smart_fan.automation": "Ventilatorautomatisering",
	"smart_fan.humidity": "Vochtdetectie",
	"smart_fan.humidity_body": "Start de ventilator als de lucht vochtig wordt.",
	"smart_fan.sensitivity": "Gevoeligheid",
	"smart_fan.schedule": "Ventilatorschema",
	"smart_fan.schedule_body": "Maak er een met de actie set_fan_schedule of in de Konnect-app; zet hem hier aan of uit.",
	"smart_fan.every_day": "Elke dag",
	"smart_fan.once": "Eenmalig",
	"toilet.cleaning": "Reiniging",
	"toilet.occupied": "Bezet",
	"ice_bath.week_of": "Deze week: {done} van {target}",
	"ice_bath.week": "Deze week: {done}"
}, tt = {
	"app.title": "Kohler Konnect",
	"app.loading": "Łączenie z Kohler Konnect…",
	"app.no_devices": "Nie ma jeszcze urządzeń Kohler",
	"app.no_devices_body": "Urządzenia z Twojego konta Kohler Konnect pojawią się tutaj, gdy integracja je odczyta.",
	"app.stale_bundle": "Panel został zaktualizowany. Odśwież, aby wczytać nową wersję.",
	"app.refresh": "Odśwież",
	"app.not_found": "Tego urządzenia nie ma już na koncie.",
	"nav.back": "Wstecz",
	"nav.more": "Więcej",
	"nav.menu": "Menu",
	"nav.controls": "Sterowanie",
	"nav.favorites": "Ulubione",
	"nav.experiences": "Doznania",
	"nav.devices": "Urządzenia",
	"nav.usage": "Zużycie",
	"nav.account": "Konto",
	"home.welcome": "Witaj, {name}",
	"home.welcome_anonymous": "Witaj",
	"home.help": "Potrzebujesz pomocy?",
	"home.summary_quiet": "Wszystko spokojnie",
	"home.summary_running": "Aktywne: {count}",
	"home.summary_alert": "Wymagające uwagi: {count}",
	"home.other_area": "Inne",
	"home.usage_this_month": "W tym miesiącu",
	"home.usage_today": "Dzisiaj",
	"home.usage_last_7_days": "Ostatnie 7 dni",
	"status.idle": "Bezczynny",
	"status.ready": "Gotowy",
	"status.off": "Wyłączony",
	"status.on": "Włączony",
	"status.offline": "Offline",
	"status.running": "Pracuje",
	"status.warming": "Wstępne podgrzewanie",
	"status.paused": "Wstrzymany",
	"status.water_on": "Woda odkręcona",
	"status.dispensing": "Nalewanie",
	"status.leak": "Wykryto wyciek",
	"status.problem": "Wymaga uwagi",
	"status.minutes_left": "{time} do końca",
	"status.untested": "Nietestowane",
	"shower.simple_start": "Prosty start",
	"shower.simple_start_unknown": "Zawór nie zgłosił jeszcze domyślnego prysznica",
	"shower.outlets": "Wyjścia",
	"shower.zone": "Strefa {zone}",
	"shower.temperature": "Temperatura",
	"shower.flow": "Przepływ",
	"shower.start": "Uruchom",
	"shower.stop": "Zatrzymaj",
	"shower.resume": "Wznów",
	"shower.cold": "Zimna",
	"shower.staged_hint": "Wybierz wyjścia, a następnie naciśnij Uruchom",
	"shower.locked_warming": "Zmiany są zablokowane podczas wstępnego podgrzewania",
	"shower.locked": "Zablokowane",
	"shower.set_to": "Ustawiono na {temperature}",
	"shower.at_temperature": "Temperatura osiągnięta",
	"shower.heating": "Osiąganie temperatury",
	"shower.warming_title": "Wstępne podgrzewanie",
	"shower.running_title": "Woda płynie",
	"shower.paused_title": "Czeka na Ciebie",
	"shower.paused_body": "Wstępne podgrzewanie zakończone. Wznów, aby rozpocząć prysznic.",
	"shower.time_left": "do końca",
	"scenes.empty_favorites": "Nie ma jeszcze ulubionych",
	"scenes.empty_favorites_body": "Tutaj pojawią się ulubione ustawienia utworzone w aplikacji Konnect lub na ekranie dotykowym.",
	"scenes.empty_experiences": "Nie ma jeszcze doznań",
	"scenes.empty_experiences_body": "Tutaj pojawią się doznania dodane w aplikacji Konnect.",
	"scenes.start": "Uruchom",
	"scenes.running": "Aktywne",
	"controller.steam": "Para",
	"controller.steam_start": "Włącz parę",
	"controller.steam_stop": "Wyłącz parę",
	"controller.steam_blocked": "Para nie może działać, gdy działa prysznic",
	"controller.status": "Status",
	"controller.music": "Muzyka",
	"controller.light": "Światło",
	"controller.read_only": "Tylko do odczytu: sterownik uruchamia ulubione ustawienia, a nie pojedyncze wyjścia.",
	"faucet.faucet": "Kran",
	"faucet.dispense_amount": "Ilość do nalania",
	"faucet.start": "Uruchom",
	"faucet.stop": "Zatrzymaj",
	"faucet.see_more": "Pokaż więcej",
	"faucet.see_less": "Pokaż mniej",
	"faucet.presets": "Ustawienia wstępne",
	"faucet.no_presets": "Tutaj pojawią się ustawienia wstępne utworzone w aplikacji Konnect.",
	"faucet.custom": "Własna ilość",
	"faucet.auto_off": "Automatyczne zakręcenie za {time}",
	"faucet.handle_closed": "Otwórz dźwignię, aby zdalnie odkręcić wodę.",
	"faucet.firmware_downloading": "Kran pobiera oprogramowanie układowe. Do zakończenia pobierania nie można odkręcić wody.",
	"faucet.leak": "Kohler zgłosił wyciek przy tym kranie.",
	"faucet.clear_leak": "Wyczyść",
	"faucet.last_dispensed": "Ostatnio nalano {amount}",
	"settings.title": "Ustawienia",
	"settings.locked_running": "Nie można zmieniać ustawień, gdy płynie woda.",
	"settings.defaults": "Ustawienia domyślne",
	"settings.defaults_body": "Ustawione tu wartości są domyślnie stosowane po uruchomieniu prysznica.",
	"settings.default_temperature": "Temperatura",
	"settings.default_outlets": "Wyjścia",
	"settings.default_outlets_body": "Zmień domyślne wyjścia w aplikacji Konnect.",
	"settings.device_control": "Sterowanie urządzeniem",
	"settings.warmup": "Wstępne podgrzewanie",
	"settings.warmup_body": "Podgrzewa wodę przed rozpoczęciem prysznica, a potem czeka na Ciebie.",
	"settings.warmup_outlets": "Wyjścia do podgrzania",
	"settings.warmup_auto_restore": "Autoprzywracanie podgrzewania",
	"settings.warmup_auto_restore_body": "Przywraca wstępne podgrzewanie, gdy coś po cichu je wyłączy.",
	"settings.max_temperature": "Maksymalna temperatura",
	"settings.max_duration": "Maksymalny czas prysznica",
	"settings.flow": "Przepływ",
	"settings.general": "Ogólne",
	"settings.about": "Informacje",
	"settings.water_usage": "Zużycie wody",
	"settings.diagnostics": "Diagnostyka",
	"settings.restart": "Uruchom ponownie produkt",
	"settings.save": "Zapisz",
	"settings.cancel": "Anuluj",
	"settings.dispense_defaults": "Nalewanie",
	"settings.dispense_amount": "Ilość do nalania",
	"settings.preset": "Ustawienie wstępne",
	"settings.safety_limit": "Limit bezpieczeństwa wody",
	"settings.safety_limit_body": "Ustawiany dla całego konta na karcie Konto.",
	"settings.web_settings": "Strona ustawień sterownika",
	"settings.scald_title": "Zmienić limit ochrony przed poparzeniem?",
	"settings.scald_body": "Maksymalna temperatura to najgorętsza woda, jaką może dostarczyć ten prysznic. Podniesienie jej do {temperature} pozwoli każdemu wyjściu podawać tak gorącą wodę.",
	"settings.scald_confirm": "Zmień limit",
	"settings.restart_title": "Uruchomić ponownie urządzenie {name}?",
	"settings.restart_body": "Zawór uruchomi się ponownie, tak jak po wybraniu „Uruchom ponownie produkt” w aplikacji Konnect. Płynąca woda zostanie zakręcona.",
	"settings.restart_confirm": "Uruchom ponownie",
	"settings.restart_done": "Wysłano polecenie ponownego uruchomienia.",
	"settings.touchscreen": "Ekran dotykowy",
	"settings.touchscreen_body": "Każda zmiana trafia naraz do wszystkich paneli, tak jak w Panelu sterowania aplikacji Konnect.",
	"settings.panel_language": "Język panelu",
	"settings.panel_units": "Jednostki panelu",
	"settings.wake_screen": "Ekran po wybudzeniu",
	"settings.display_brightness": "Jasność ekranu",
	"settings.haptic_feedback": "Wibracje dotykowe",
	"settings.accessible_display": "Ekran dostępności",
	"settings.accessible_display_body": "Dodaje przyciski na ekranie, które odwzorowują pokrętło. Aktualizacja paneli może chwilę potrwać.",
	"settings.flow_control": "Sterowanie przepływem",
	"settings.flow_control_body": "Pozwala regulować przepływ na głównym ekranie panelu.",
	"settings.water_saving_mode": "Tryb oszczędzania wody",
	"settings.water_saving_mode_body": "Ustawiany przy instalacji prysznica: jeden wylot naraz. Aplikacja Konnect też go nie zmienia.",
	"about.title": "Informacje",
	"about.model": "Model",
	"about.serial": "Numer seryjny",
	"about.install_date": "Data instalacji",
	"about.registered": "Data rejestracji",
	"about.hardware": "Sprzęt",
	"about.firmware": "Oprogramowanie układowe",
	"about.firmware.interface": "Interfejs",
	"about.firmware.valve": "Zawór",
	"about.firmware.second_valve": "Drugi zawór",
	"about.firmware.gateway": "Brama",
	"about.firmware.controller": "Sterownik",
	"about.firmware.application": "Aplikacja",
	"about.firmware.bootloader": "Program rozruchowy",
	"about.firmware.os": "System operacyjny",
	"about.firmware.certificate": "Certyfikat",
	"about.firmware.service_pack": "Pakiet serwisowy",
	"about.up_to_date": "Aktualne",
	"about.update_available": "Dostępna aktualizacja: {version}",
	"about.update_in_app": "Zainstaluj aktualizacje w aplikacji Konnect.",
	"about.technical": "Informacje techniczne",
	"about.gateway": "Brama",
	"about.valves": "Zawory",
	"about.interfaces": "Interfejsy",
	"about.status": "Status",
	"about.leak_history": "Historia wycieków",
	"about.no_leaks": "Brak zgłoszonych wycieków",
	"about.unknown": "Brak danych",
	"usage.title": "Zużycie wody",
	"usage.week": "Tydzień",
	"usage.month": "Miesiąc",
	"usage.year": "Rok",
	"usage.average": "Średnio",
	"usage.high": "Najwięcej",
	"usage.total": "Łącznie",
	"usage.water": "Woda",
	"usage.time": "Czas",
	"usage.per_day": "dziennie",
	"usage.previous": "Poprzedni okres",
	"usage.next": "Następny okres",
	"usage.calendar_week": "Tydzień kalendarzowy. Czujnik „w tym tygodniu” obejmuje ostatnie siedem dni.",
	"usage.error": "Kohler nie zwrócił danych o zużyciu za ten okres.",
	"usage.retry": "Spróbuj ponownie",
	"usage.equivalent_bath": "Pełne wanny: około {count}",
	"diagnostics.title": "Diagnostyka",
	"diagnostics.stream": "Połączenie z Kohler",
	"diagnostics.stream_body": "Bieżące połączenie Home Assistant z chmurą Kohler.",
	"diagnostics.cloud": "Urządzenie online",
	"diagnostics.cloud_body": "Informuje, czy chmura Kohler może połączyć się z tym urządzeniem.",
	"diagnostics.connected": "Połączono",
	"diagnostics.disconnected": "Rozłączono",
	"diagnostics.unknown": "Jeszcze nie sprawdzono",
	"diagnostics.last_update": "Ostatnia aktualizacja",
	"diagnostics.faults": "Aktywne usterki",
	"diagnostics.no_faults": "Brak aktywnych usterek",
	"diagnostics.clear_errors": "Wyczyść dziennik błędów",
	"diagnostics.report_log": "Report Log",
	"diagnostics.report_log_body": "Zapisuje każdą wiadomość Kohler na potrzeby zgłoszenia błędu.",
	"diagnostics.download": "Pobierz diagnostykę",
	"diagnostics.download_body": "Jeden raport całej instalacji z ukrytymi danymi logowania i numerami seryjnymi.",
	"account.admin_only": "Ustawienia konta są dostępne dla administratorów.",
	"account.connection": "Połączenie z Kohler",
	"account.zone_grouping": "Grupowanie stref",
	"account.zone_grouping.numbered": "Numery stref",
	"account.zone_grouping.subdevices": "Osobne urządzenie dla każdej strefy",
	"account.zone_grouping.outlet_labels": "Nazwy wyjść przy sterowaniu",
	"account.safety_limit": "Limit bezpieczeństwa wody",
	"account.safety_limit_body": "Zakręca wodę w kranie po tym czasie od jej odkręcenia z Home Assistant. 0 wyłącza limit.",
	"account.minutes": "{count} min",
	"account.off": "Wyłączony",
	"account.sidebar_panel": "Pokaż ten panel na pasku bocznym",
	"account.sidebar_panel_body": "Wyłączenie usuwa panel dla wszystkich użytkowników.",
	"account.save": "Zapisz",
	"account.saved": "Zapisano",
	"account.reload_warning": "Zapisanie powoduje kilkusekundowe ponowne łączenie z Kohler.",
	"dialog.cancel": "Anuluj",
	"dialog.confirm": "Potwierdź",
	"control.decrease": "{name}: zmniejsz",
	"control.increase": "{name}: zwiększ",
	"generic.controls": "Sterowanie",
	"generic.readings": "Odczyty",
	"generic.press": "Naciśnij",
	"generic.press_named": "Naciśnij: {name}",
	"generic.brightness": "{name}: jasność",
	"generic.effect": "{name}: efekt",
	"generic.untested_body": "Te elementy sterujące działają dokładnie jak w aplikacji Konnect, ale nie zostały jeszcze potwierdzone na prawdziwym sprzęcie.",
	"generic.ozone_title": "Nie wchodź pod prysznic",
	"generic.ozone_body": "Czyszczenie ozonem przepuszcza przez prysznic wodę z ozonem, aby go wyczyścić. Upewnij się, że nikogo nie ma pod prysznicem i że nikt nie wejdzie, dopóki czyszczenie się nie zakończy.",
	"generic.ozone_confirm": "Rozpocznij czyszczenie",
	"unit.minutes_short": "{count} min",
	"unit.hours_minutes": "{hours} godz. {minutes} min",
	"smart_fan.fan": "Wentylator",
	"smart_fan.light": "Światło",
	"smart_fan.on": "Włączone",
	"smart_fan.fan_on": "Wentylator włączony",
	"smart_fan.light_on": "Światło włączone",
	"smart_fan.fan_favorites": "Ulubione wentylatora",
	"smart_fan.light_favorites": "Ulubione światła",
	"smart_fan.stop_all": "Zatrzymaj wszystko",
	"smart_fan.locked_running": "Ustawień wentylatora i światła nie można zmieniać w trakcie ich pracy.",
	"smart_fan.defaults_body": "Co robią wentylator i światło po uruchomieniu bez ulubionego ustawienia.",
	"smart_fan.fan_speed": "Prędkość wentylatora",
	"smart_fan.fan_timer": "Wyłącznik czasowy wentylatora",
	"smart_fan.fan_timer_body": "Wyłącza wentylator po upływie czasu pracy.",
	"smart_fan.fan_run_time": "Czas pracy wentylatora",
	"smart_fan.light_color": "Kolor światła",
	"smart_fan.light_brightness": "Jasność światła",
	"smart_fan.circadian": "Oświetlenie dobowe",
	"smart_fan.circadian_body": "Światło zmienia się wraz z porą dnia zamiast mieć stały kolor.",
	"smart_fan.audible_feedback": "Sygnał dźwiękowy",
	"smart_fan.audible_feedback_body": "Sygnalizuje dźwiękiem działania z pilota i aplikacji.",
	"smart_fan.automation": "Automatyzacja wentylatora",
	"smart_fan.humidity": "Wykrywanie wilgotności",
	"smart_fan.humidity_body": "Włącza wentylator, gdy powietrze staje się wilgotne.",
	"smart_fan.sensitivity": "Czułość",
	"smart_fan.schedule": "Harmonogram wentylatora",
	"smart_fan.schedule_body": "Utwórz go akcją set_fan_schedule lub w aplikacji Konnect; tutaj go włączysz lub wyłączysz.",
	"smart_fan.every_day": "Codziennie",
	"smart_fan.once": "Jednorazowo",
	"toilet.cleaning": "Czyszczenie",
	"toilet.occupied": "Zajęte",
	"ice_bath.week_of": "W tym tygodniu: {done} z {target}",
	"ice_bath.week": "W tym tygodniu: {done}"
}, nt = {
	"app.title": "Kohler Konnect",
	"app.loading": "Conectando ao Kohler Konnect…",
	"app.no_devices": "Ainda não há dispositivos Kohler",
	"app.no_devices_body": "Os dispositivos da sua conta Kohler Konnect aparecerão aqui depois que a integração os ler.",
	"app.stale_bundle": "O painel foi atualizado. Recarregue a página para usar a nova versão.",
	"app.refresh": "Recarregar",
	"app.not_found": "Este dispositivo não está mais na conta.",
	"nav.back": "Voltar",
	"nav.more": "Mais opções",
	"nav.menu": "Menu",
	"nav.controls": "Controles",
	"nav.favorites": "Favoritos",
	"nav.experiences": "Experiências",
	"nav.devices": "Dispositivos",
	"nav.usage": "Consumo",
	"nav.account": "Conta",
	"home.welcome": "Olá, {name}",
	"home.welcome_anonymous": "Olá",
	"home.help": "Precisa de ajuda?",
	"home.summary_quiet": "Tudo tranquilo",
	"home.summary_running": "Em funcionamento: {count}",
	"home.summary_alert": "Precisa de atenção: {count}",
	"home.other_area": "Outros",
	"home.usage_this_month": "Este mês",
	"home.usage_today": "Hoje",
	"home.usage_last_7_days": "Últimos 7 dias",
	"status.idle": "Ocioso",
	"status.ready": "Pronto",
	"status.off": "Desligado",
	"status.on": "Ligado",
	"status.offline": "Offline",
	"status.running": "Ligado",
	"status.warming": "Pré-aquecendo",
	"status.paused": "Em pausa",
	"status.water_on": "Água aberta",
	"status.dispensing": "Dosando",
	"status.leak": "Vazamento detectado",
	"status.problem": "Precisa de atenção",
	"status.minutes_left": "Faltam {time}",
	"status.untested": "Não testado",
	"shower.simple_start": "Início simples",
	"shower.simple_start_unknown": "A válvula ainda não informou seu banho padrão",
	"shower.outlets": "Saídas",
	"shower.zone": "Zona {zone}",
	"shower.temperature": "Temperatura",
	"shower.flow": "Vazão",
	"shower.start": "Iniciar",
	"shower.stop": "Parar",
	"shower.resume": "Retomar",
	"shower.cold": "Fria",
	"shower.staged_hint": "Escolha as saídas e pressione Iniciar",
	"shower.locked_warming": "As alterações ficam bloqueadas enquanto a válvula pré-aquece",
	"shower.locked": "Bloqueado",
	"shower.set_to": "Ajustada para {temperature}",
	"shower.at_temperature": "Temperatura atingida",
	"shower.heating": "Atingindo a temperatura",
	"shower.warming_title": "Pré-aquecendo",
	"shower.running_title": "Água aberta",
	"shower.paused_title": "Tudo pronto quando você quiser",
	"shower.paused_body": "Pré-aquecimento concluído. Retome para começar seu banho.",
	"shower.time_left": "restante",
	"scenes.empty_favorites": "Ainda não há favoritos",
	"scenes.empty_favorites_body": "Os favoritos criados no app Konnect ou na tela sensível ao toque aparecem aqui.",
	"scenes.empty_experiences": "Ainda não há experiências",
	"scenes.empty_experiences_body": "As experiências adicionadas no app Konnect aparecem aqui.",
	"scenes.start": "Iniciar",
	"scenes.running": "Em execução",
	"controller.steam": "Vapor",
	"controller.steam_start": "Iniciar vapor",
	"controller.steam_stop": "Parar vapor",
	"controller.steam_blocked": "O vapor não funciona com o chuveiro ligado",
	"controller.status": "Status",
	"controller.music": "Música",
	"controller.light": "Luz",
	"controller.read_only": "Somente leitura: o controlador executa favoritos, não saídas individuais.",
	"faucet.faucet": "Torneira",
	"faucet.dispense_amount": "Quantidade a dosar",
	"faucet.start": "Iniciar",
	"faucet.stop": "Parar",
	"faucet.see_more": "Ver mais",
	"faucet.see_less": "Ver menos",
	"faucet.presets": "Predefinições",
	"faucet.no_presets": "As predefinições criadas no app Konnect aparecem aqui.",
	"faucet.custom": "Quantidade personalizada",
	"faucet.auto_off": "Fecha automaticamente em {time}",
	"faucet.handle_closed": "Abra a alavanca para usar a água remotamente.",
	"faucet.firmware_downloading": "A torneira está baixando o firmware. Não é possível abrir a água até o download terminar.",
	"faucet.leak": "A Kohler informou um vazamento nesta torneira.",
	"faucet.clear_leak": "Limpar",
	"faucet.last_dispensed": "Última dosagem: {amount}",
	"settings.title": "Configurações",
	"settings.locked_running": "As configurações não podem ser alteradas enquanto a água estiver aberta.",
	"settings.defaults": "Configurações padrão",
	"settings.defaults_body": "Os valores definidos aqui são aplicados por padrão quando você inicia o banho.",
	"settings.default_temperature": "Temperatura",
	"settings.default_outlets": "Saídas",
	"settings.default_outlets_body": "Altere as saídas padrão no app Konnect.",
	"settings.device_control": "Controle do dispositivo",
	"settings.warmup": "Pré-aquecimento",
	"settings.warmup_body": "Aquece a água antes do banho e depois espera até que você queira começar.",
	"settings.warmup_outlets": "Saídas a pré-aquecer",
	"settings.warmup_auto_restore": "Restaurar pré-aquecimento",
	"settings.warmup_auto_restore_body": "Reativa o pré-aquecimento quando algo o desativa sem aviso.",
	"settings.max_temperature": "Temperatura máxima",
	"settings.max_duration": "Duração máxima do banho",
	"settings.flow": "Vazão",
	"settings.general": "Geral",
	"settings.about": "Sobre",
	"settings.water_usage": "Consumo de água",
	"settings.diagnostics": "Diagnósticos",
	"settings.restart": "Reiniciar produto",
	"settings.save": "Salvar",
	"settings.cancel": "Cancelar",
	"settings.dispense_defaults": "Dosagem",
	"settings.dispense_amount": "Quantidade a dosar",
	"settings.preset": "Predefinição",
	"settings.safety_limit": "Limite de segurança da água",
	"settings.safety_limit_body": "Definido para toda a conta em Conta.",
	"settings.web_settings": "Configurações web do controlador",
	"settings.scald_title": "Alterar o limite contra queimaduras?",
	"settings.scald_body": "A temperatura máxima é a água mais quente que este chuveiro pode fornecer. Se você aumentá-la para {temperature}, todas as saídas poderão chegar a essa temperatura.",
	"settings.scald_confirm": "Alterar limite",
	"settings.restart_title": "Reiniciar {name}?",
	"settings.restart_body": "A válvula reinicia, como faz a opção Reiniciar produto do app Konnect. Se houver água aberta, ela é fechada.",
	"settings.restart_confirm": "Reiniciar",
	"settings.restart_done": "Reinício enviado.",
	"settings.touchscreen": "Tela sensível ao toque",
	"settings.touchscreen_body": "Cada alteração é gravada em todos os painéis de uma vez, como faz o Painel de controle do app Konnect.",
	"settings.panel_language": "Idioma do painel",
	"settings.panel_units": "Unidades do painel",
	"settings.wake_screen": "Tela ao despertar",
	"settings.display_brightness": "Brilho da tela",
	"settings.haptic_feedback": "Resposta tátil",
	"settings.accessible_display": "Tela acessível",
	"settings.accessible_display_body": "Adiciona controles na tela que reproduzem o botão giratório. Os painéis podem levar alguns instantes para atualizar.",
	"settings.flow_control": "Controle de vazão",
	"settings.flow_control_body": "Permite ajustar a vazão na tela principal do painel.",
	"settings.water_saving_mode": "Modo de economia de água",
	"settings.water_saving_mode_body": "Definido na instalação do chuveiro: uma saída por vez. O app Konnect também não o altera.",
	"about.title": "Sobre",
	"about.model": "Modelo",
	"about.serial": "Número de série",
	"about.install_date": "Data de instalação",
	"about.registered": "Registrado",
	"about.hardware": "Hardware",
	"about.firmware": "Firmware",
	"about.firmware.interface": "Interface",
	"about.firmware.valve": "Válvula",
	"about.firmware.second_valve": "Segunda válvula",
	"about.firmware.gateway": "Gateway",
	"about.firmware.controller": "Controlador",
	"about.firmware.application": "Aplicação",
	"about.firmware.bootloader": "Bootloader",
	"about.firmware.os": "Sistema operacional",
	"about.firmware.certificate": "Certificado",
	"about.firmware.service_pack": "Service pack",
	"about.up_to_date": "Atualizado",
	"about.update_available": "Atualização disponível: {version}",
	"about.update_in_app": "Instale as atualizações pelo app Konnect.",
	"about.technical": "Informações técnicas",
	"about.gateway": "Gateway",
	"about.valves": "Válvulas",
	"about.interfaces": "Interfaces",
	"about.status": "Status",
	"about.leak_history": "Histórico de vazamentos",
	"about.no_leaks": "Nenhum vazamento informado",
	"about.unknown": "Não informado",
	"usage.title": "Consumo de água",
	"usage.week": "Semana",
	"usage.month": "Mês",
	"usage.year": "Ano",
	"usage.average": "Média",
	"usage.high": "Máximo",
	"usage.total": "Total",
	"usage.water": "Água",
	"usage.time": "Tempo",
	"usage.per_day": "por dia",
	"usage.previous": "Período anterior",
	"usage.next": "Próximo período",
	"usage.calendar_week": "Semana do calendário. Os sensores semanais abrangem os últimos sete dias.",
	"usage.error": "A Kohler não retornou o consumo deste período.",
	"usage.retry": "Tentar novamente",
	"usage.equivalent_bath": "Banheiras cheias de água: cerca de {count}",
	"diagnostics.title": "Diagnósticos",
	"diagnostics.stream": "Conexão com a Kohler",
	"diagnostics.stream_body": "A conexão em tempo real do Home Assistant com a nuvem da Kohler.",
	"diagnostics.cloud": "Dispositivo online",
	"diagnostics.cloud_body": "Se a nuvem da Kohler consegue alcançar este dispositivo.",
	"diagnostics.connected": "Conectado",
	"diagnostics.disconnected": "Desconectado",
	"diagnostics.unknown": "Ainda não verificado",
	"diagnostics.last_update": "Última atualização",
	"diagnostics.faults": "Falhas ativas",
	"diagnostics.no_faults": "Nenhuma falha ativa",
	"diagnostics.clear_errors": "Limpar registro de erros",
	"diagnostics.report_log": "Report Log",
	"diagnostics.report_log_body": "Registra todas as mensagens da Kohler para um relatório de erro.",
	"diagnostics.download": "Baixar diagnósticos",
	"diagnostics.download_body": "Um relatório de toda a instalação, com as credenciais e os números de série ocultos.",
	"account.admin_only": "As configurações da conta estão disponíveis apenas para administradores.",
	"account.connection": "Conexão com a Kohler",
	"account.zone_grouping": "Agrupamento de zonas",
	"account.zone_grouping.numbered": "Números de zona",
	"account.zone_grouping.subdevices": "Um subdispositivo por zona",
	"account.zone_grouping.outlet_labels": "Nomes das saídas nos controles",
	"account.safety_limit": "Limite de segurança da água",
	"account.safety_limit_body": "Fecha a água da torneira este tempo depois de ela ser aberta pelo Home Assistant. 0 desativa o limite.",
	"account.minutes": "{count} min",
	"account.off": "Desligado",
	"account.sidebar_panel": "Mostrar este painel na barra lateral",
	"account.sidebar_panel_body": "Desativá-lo remove o painel para todos os usuários.",
	"account.save": "Salvar",
	"account.saved": "Salvo",
	"account.reload_warning": "Ao salvar, a conexão com a Kohler é reiniciada por alguns segundos.",
	"dialog.cancel": "Cancelar",
	"dialog.confirm": "Confirmar",
	"control.decrease": "Diminuir {name}",
	"control.increase": "Aumentar {name}",
	"generic.controls": "Controles",
	"generic.readings": "Leituras",
	"generic.press": "Pressionar",
	"generic.press_named": "Pressionar {name}",
	"generic.brightness": "{name}: brilho",
	"generic.effect": "{name}: efeito",
	"generic.untested_body": "Estes controles reproduzem exatamente o app Konnect, mas ainda não foram confirmados em hardware real.",
	"generic.ozone_title": "Não entre no chuveiro",
	"generic.ozone_body": "Uma limpeza com ozônio passa água ozonizada pelo chuveiro para limpá-lo. Verifique se não há ninguém no chuveiro e se ninguém vai entrar até a limpeza terminar.",
	"generic.ozone_confirm": "Iniciar limpeza",
	"unit.minutes_short": "{count} min",
	"unit.hours_minutes": "{hours} h {minutes} min",
	"smart_fan.fan": "Ventilador",
	"smart_fan.light": "Luz",
	"smart_fan.on": "Ligado",
	"smart_fan.fan_on": "Ventilador ligado",
	"smart_fan.light_on": "Luz ligada",
	"smart_fan.fan_favorites": "Favoritos do ventilador",
	"smart_fan.light_favorites": "Favoritos da luz",
	"smart_fan.stop_all": "Parar tudo",
	"smart_fan.locked_running": "Uma configuração do ventilador ou da luz não pode ser alterada enquanto estiver em funcionamento.",
	"smart_fan.defaults_body": "O que o ventilador e a luz fazem quando ligados sem um favorito.",
	"smart_fan.fan_speed": "Velocidade do ventilador",
	"smart_fan.fan_timer": "Temporizador do ventilador",
	"smart_fan.fan_timer_body": "Desliga o ventilador ao fim do tempo de funcionamento.",
	"smart_fan.fan_run_time": "Tempo de funcionamento",
	"smart_fan.light_color": "Cor da luz",
	"smart_fan.light_brightness": "Brilho da luz",
	"smart_fan.circadian": "Iluminação circadiana",
	"smart_fan.circadian_body": "A luz acompanha a hora do dia em vez de uma cor fixa.",
	"smart_fan.audible_feedback": "Aviso sonoro",
	"smart_fan.audible_feedback_body": "Emite bipes nas ações do controle remoto e do app.",
	"smart_fan.automation": "Automação do ventilador",
	"smart_fan.humidity": "Detecção de umidade",
	"smart_fan.humidity_body": "Liga o ventilador quando o ar fica úmido.",
	"smart_fan.sensitivity": "Sensibilidade",
	"smart_fan.schedule": "Programação do ventilador",
	"smart_fan.schedule_body": "Crie uma com a ação set_fan_schedule ou no app Konnect; ative ou desative aqui.",
	"smart_fan.every_day": "Todos os dias",
	"smart_fan.once": "Uma vez",
	"toilet.cleaning": "Limpando",
	"toilet.occupied": "Em uso",
	"ice_bath.week_of": "Esta semana: {done} de {target}",
	"ice_bath.week": "Esta semana: {done}"
}, M = {
	en: Ye,
	de: Je,
	es: Ze,
	"es-419": Xe,
	fr: Qe,
	it: $e,
	nl: et,
	pl: tt,
	"pt-BR": nt,
	pt: nt,
	sv: {
		"app.title": "Kohler Konnect",
		"app.loading": "Ansluter till Kohler Konnect…",
		"app.no_devices": "Inga Kohler-enheter än",
		"app.no_devices_body": "Enheter på ditt Kohler Konnect-konto visas här när integrationen har läst in dem.",
		"app.stale_bundle": "Panelen har uppdaterats. Uppdatera för att läsa in den nya versionen.",
		"app.refresh": "Uppdatera",
		"app.not_found": "Den här enheten finns inte längre på kontot.",
		"nav.back": "Tillbaka",
		"nav.more": "Mer",
		"nav.menu": "Meny",
		"nav.controls": "Reglage",
		"nav.favorites": "Favoriter",
		"nav.experiences": "Upplevelser",
		"nav.devices": "Enheter",
		"nav.usage": "Förbrukning",
		"nav.account": "Konto",
		"home.welcome": "Välkommen, {name}",
		"home.welcome_anonymous": "Välkommen",
		"home.help": "Behöver du hjälp?",
		"home.summary_quiet": "Allt lugnt",
		"home.summary_running": "Igång: {count}",
		"home.summary_alert": "Kräver åtgärd: {count}",
		"home.other_area": "Övrigt",
		"home.usage_this_month": "Denna månad",
		"home.usage_today": "I dag",
		"home.usage_last_7_days": "Senaste 7 dagarna",
		"status.idle": "Inaktiv",
		"status.ready": "Redo",
		"status.off": "Av",
		"status.on": "På",
		"status.offline": "Offline",
		"status.running": "Igång",
		"status.warming": "Förvärmning",
		"status.paused": "Pausad",
		"status.water_on": "Vatten på",
		"status.dispensing": "Tappning",
		"status.leak": "Läcka upptäckt",
		"status.problem": "Kräver åtgärd",
		"status.minutes_left": "{time} kvar",
		"status.untested": "Otestad",
		"shower.simple_start": "Snabbstart",
		"shower.simple_start_unknown": "Ventilen har inte rapporterat sin standarddusch än",
		"shower.outlets": "Utlopp",
		"shower.zone": "Zon {zone}",
		"shower.temperature": "Temperatur",
		"shower.flow": "Flöde",
		"shower.start": "Starta",
		"shower.stop": "Stoppa",
		"shower.resume": "Återuppta",
		"shower.cold": "Kallt",
		"shower.staged_hint": "Välj utlopp och tryck sedan på Starta",
		"shower.locked_warming": "Ändringar är låsta medan ventilen förvärmer",
		"shower.locked": "Låst",
		"shower.set_to": "Inställd på {temperature}",
		"shower.at_temperature": "Temperatur uppnådd",
		"shower.heating": "Värms upp",
		"shower.warming_title": "Förvärmning",
		"shower.running_title": "Vattnet rinner",
		"shower.paused_title": "Redo när du vill",
		"shower.paused_body": "Förvärmningen är klar. Tryck på Återuppta för att starta duschen.",
		"shower.time_left": "kvar",
		"scenes.empty_favorites": "Inga favoriter än",
		"scenes.empty_favorites_body": "Favoriter som du skapar i Konnect-appen eller på pekskärmen visas här.",
		"scenes.empty_experiences": "Inga upplevelser än",
		"scenes.empty_experiences_body": "Upplevelser som du lägger till i Konnect-appen visas här.",
		"scenes.start": "Starta",
		"scenes.running": "Igång",
		"controller.steam": "Ånga",
		"controller.steam_start": "Starta ånga",
		"controller.steam_stop": "Stoppa ånga",
		"controller.steam_blocked": "Ånga kan inte köras medan duschen är igång",
		"controller.status": "Status",
		"controller.music": "Musik",
		"controller.light": "Ljus",
		"controller.read_only": "Endast visning: styrenheten kör favoriter, inte enskilda utlopp.",
		"faucet.faucet": "Kran",
		"faucet.dispense_amount": "Tappmängd",
		"faucet.start": "Starta",
		"faucet.stop": "Stoppa",
		"faucet.see_more": "Visa mer",
		"faucet.see_less": "Visa mindre",
		"faucet.presets": "Förinställningar",
		"faucet.no_presets": "Förinställningar som du skapar i Konnect-appen visas här.",
		"faucet.custom": "Egen mängd",
		"faucet.auto_off": "Stängs av automatiskt om {time}",
		"faucet.handle_closed": "Öppna handtaget för att kunna sätta på vattnet på distans.",
		"faucet.firmware_downloading": "Kranen laddar ned firmware. Vattnet kan inte sättas på förrän det är klart.",
		"faucet.leak": "Kohler har rapporterat en läcka vid den här kranen.",
		"faucet.clear_leak": "Rensa",
		"faucet.last_dispensed": "Tappade senast {amount}",
		"settings.title": "Inställningar",
		"settings.locked_running": "Inställningarna kan inte ändras medan vattnet rinner.",
		"settings.defaults": "Standardinställningar",
		"settings.defaults_body": "Värdena här används som standard när du startar duschen.",
		"settings.default_temperature": "Temperatur",
		"settings.default_outlets": "Utlopp",
		"settings.default_outlets_body": "Ändra standardutloppen i Konnect-appen.",
		"settings.device_control": "Enhetsstyrning",
		"settings.warmup": "Duschförvärmning",
		"settings.warmup_body": "Värmer vattnet innan duschen startar och väntar sedan tills du är redo.",
		"settings.warmup_outlets": "Utlopp att förvärma",
		"settings.warmup_auto_restore": "Återställ förvärmning",
		"settings.warmup_auto_restore_body": "Slår på förvärmningen igen om något stänger av den obemärkt.",
		"settings.max_temperature": "Maxtemperatur",
		"settings.max_duration": "Maximal duschtid",
		"settings.flow": "Flöde",
		"settings.general": "Allmänt",
		"settings.about": "Om",
		"settings.water_usage": "Vattenförbrukning",
		"settings.diagnostics": "Diagnostik",
		"settings.restart": "Starta om produkten",
		"settings.save": "Spara",
		"settings.cancel": "Avbryt",
		"settings.dispense_defaults": "Tappning",
		"settings.dispense_amount": "Tappmängd",
		"settings.preset": "Förinställning",
		"settings.safety_limit": "Säkerhetsgräns för vatten",
		"settings.safety_limit_body": "Ställs in för hela kontot under Konto.",
		"settings.web_settings": "Styrenhetens webbinställningar",
		"settings.scald_title": "Ändra skållningsskyddet?",
		"settings.scald_body": "Maxtemperatur avgör hur varmt vattnet från den här duschen kan bli. Höjer du den till {temperature} kan alla utlopp ge så varmt vatten.",
		"settings.scald_confirm": "Ändra gräns",
		"settings.restart_title": "Starta om {name}?",
		"settings.restart_body": "Ventilen startar om, precis som med ”Starta om produkten” i Konnect-appen. Vatten som rinner stängs av.",
		"settings.restart_confirm": "Starta om",
		"settings.restart_done": "Omstart skickad.",
		"settings.touchscreen": "Pekskärm",
		"settings.touchscreen_body": "Varje ändring skrivs till alla paneler på en gång, som Kontrollpanelen i Konnect-appen gör.",
		"settings.panel_language": "Panelspråk",
		"settings.panel_units": "Panelenheter",
		"settings.wake_screen": "Väckningsskärm",
		"settings.display_brightness": "Skärmens ljusstyrka",
		"settings.haptic_feedback": "Haptisk återkoppling",
		"settings.accessible_display": "Tillgänglig skärm",
		"settings.accessible_display_body": "Lägger till kontroller på skärmen som motsvarar vredet. Panelerna kan behöva en stund för att uppdateras.",
		"settings.flow_control": "Flödesstyrning",
		"settings.flow_control_body": "Låter flödet justeras på panelens huvudskärm.",
		"settings.water_saving_mode": "Vattensparläge",
		"settings.water_saving_mode_body": "Ställs in när duschen installeras: ett utlopp i taget. Konnect-appen ändrar det inte heller.",
		"about.title": "Om",
		"about.model": "Modell",
		"about.serial": "Serienummer",
		"about.install_date": "Installationsdatum",
		"about.registered": "Registrerad",
		"about.hardware": "Hårdvara",
		"about.firmware": "Firmware",
		"about.firmware.interface": "Manöverpanel",
		"about.firmware.valve": "Ventil",
		"about.firmware.second_valve": "Andra ventilen",
		"about.firmware.gateway": "Gateway",
		"about.firmware.controller": "Styrenhet",
		"about.firmware.application": "Applikation",
		"about.firmware.bootloader": "Bootloader",
		"about.firmware.os": "Operativsystem",
		"about.firmware.certificate": "Certifikat",
		"about.firmware.service_pack": "Service pack",
		"about.up_to_date": "Uppdaterad",
		"about.update_available": "Uppdatering tillgänglig: {version}",
		"about.update_in_app": "Installera uppdateringar från Konnect-appen.",
		"about.technical": "Teknisk information",
		"about.gateway": "Gateway",
		"about.valves": "Ventiler",
		"about.interfaces": "Manöverpaneler",
		"about.status": "Status",
		"about.leak_history": "Läckagehistorik",
		"about.no_leaks": "Inga läckor rapporterade",
		"about.unknown": "Inte rapporterat",
		"usage.title": "Vattenförbrukning",
		"usage.week": "Vecka",
		"usage.month": "Månad",
		"usage.year": "År",
		"usage.average": "Genomsnitt",
		"usage.high": "Högsta",
		"usage.total": "Totalt",
		"usage.water": "Vatten",
		"usage.time": "Tid",
		"usage.per_day": "per dag",
		"usage.previous": "Föregående period",
		"usage.next": "Nästa period",
		"usage.calendar_week": "Kalendervecka. Sensorn ”denna vecka” räknar alltid de senaste sju dagarna.",
		"usage.error": "Kohler returnerade ingen förbrukning för den här perioden.",
		"usage.retry": "Försök igen",
		"usage.equivalent_bath": "Ungefär {count} badkar vatten",
		"diagnostics.title": "Diagnostik",
		"diagnostics.stream": "Kohler-anslutning",
		"diagnostics.stream_body": "Home Assistants realtidsanslutning till Kohlers moln.",
		"diagnostics.cloud": "Enhet online",
		"diagnostics.cloud_body": "Om Kohlers moln kan nå den här enheten.",
		"diagnostics.connected": "Ansluten",
		"diagnostics.disconnected": "Frånkopplad",
		"diagnostics.unknown": "Inte kontrollerat än",
		"diagnostics.last_update": "Senaste uppdatering",
		"diagnostics.faults": "Aktiva fel",
		"diagnostics.no_faults": "Inga aktiva fel",
		"diagnostics.clear_errors": "Rensa felloggen",
		"diagnostics.report_log": "Report Log",
		"diagnostics.report_log_body": "Loggar varje meddelande från Kohler för en felrapport.",
		"diagnostics.download": "Ladda ner diagnostikdata",
		"diagnostics.download_body": "En rapport över hela installationen, med inloggningsuppgifter och serienummer maskerade.",
		"account.admin_only": "Kontoinställningar är bara tillgängliga för administratörer.",
		"account.connection": "Kohler-anslutning",
		"account.zone_grouping": "Zongruppering",
		"account.zone_grouping.numbered": "Zonnummer",
		"account.zone_grouping.subdevices": "En underenhet per zon",
		"account.zone_grouping.outlet_labels": "Utloppsnamn på reglagen",
		"account.safety_limit": "Säkerhetsgräns för vatten",
		"account.safety_limit_body": "Stänger av vattnet i en kran så här länge efter att Home Assistant har slagit på det. 0 stänger av gränsen.",
		"account.minutes": "{count} min",
		"account.off": "Av",
		"account.sidebar_panel": "Visa den här panelen i sidofältet",
		"account.sidebar_panel_body": "Stängs den av försvinner panelen för alla användare.",
		"account.save": "Spara",
		"account.saved": "Sparat",
		"account.reload_warning": "När du sparar återupprättas anslutningen till Kohler, vilket tar några sekunder.",
		"dialog.cancel": "Avbryt",
		"dialog.confirm": "Bekräfta",
		"control.decrease": "Minska {name}",
		"control.increase": "Öka {name}",
		"generic.controls": "Reglage",
		"generic.readings": "Mätvärden",
		"generic.press": "Tryck",
		"generic.press_named": "Tryck på {name}",
		"generic.brightness": "{name}: ljusstyrka",
		"generic.effect": "{name}: effekt",
		"generic.untested_body": "De här reglagen följer Konnect-appen exakt men har ännu inte bekräftats på riktig hårdvara.",
		"generic.ozone_title": "Gå inte in i duschen",
		"generic.ozone_body": "En ozonrengöring skickar ozonerat vatten genom duschen för att rengöra den. Se till att ingen står i duschen och att ingen går in förrän rengöringen är klar.",
		"generic.ozone_confirm": "Starta rengöring",
		"unit.minutes_short": "{count} min",
		"unit.hours_minutes": "{hours} h {minutes} min",
		"smart_fan.fan": "Fläkt",
		"smart_fan.light": "Ljus",
		"smart_fan.on": "På",
		"smart_fan.fan_on": "Fläkt på",
		"smart_fan.light_on": "Ljus på",
		"smart_fan.fan_favorites": "Fläktfavoriter",
		"smart_fan.light_favorites": "Ljusfavoriter",
		"smart_fan.stop_all": "Stoppa allt",
		"smart_fan.locked_running": "Inställningar för fläkt och ljus kan inte ändras medan de är på.",
		"smart_fan.defaults_body": "Vad fläkten och ljuset gör när de startas utan favorit.",
		"smart_fan.fan_speed": "Fläkthastighet",
		"smart_fan.fan_timer": "Fläkttimer",
		"smart_fan.fan_timer_body": "Stänger av fläkten när körtiden har gått ut.",
		"smart_fan.fan_run_time": "Körtid för fläkt",
		"smart_fan.light_color": "Ljusfärg",
		"smart_fan.light_brightness": "Ljusstyrka",
		"smart_fan.circadian": "Dygnsrytmbelysning",
		"smart_fan.circadian_body": "Ljuset följer tiden på dygnet i stället för en fast färg.",
		"smart_fan.audible_feedback": "Ljudsignal",
		"smart_fan.audible_feedback_body": "Piper vid åtgärder från fjärrkontrollen och appen.",
		"smart_fan.automation": "Fläktautomatik",
		"smart_fan.humidity": "Fuktavkänning",
		"smart_fan.humidity_body": "Startar fläkten när luften blir fuktig.",
		"smart_fan.sensitivity": "Känslighet",
		"smart_fan.schedule": "Fläktschema",
		"smart_fan.schedule_body": "Skapa ett med åtgärden set_fan_schedule eller i Konnect-appen; slå på eller av det här.",
		"smart_fan.every_day": "Varje dag",
		"smart_fan.once": "En gång",
		"toilet.cleaning": "Rengöring",
		"toilet.occupied": "Upptagen",
		"ice_bath.week_of": "Denna vecka: {done} av {target}",
		"ice_bath.week": "Denna vecka: {done}"
	}
}, rt = Ye, it = "en", at = "en";
function ot(e) {
	let t = e || "en", n = t.split("-")[0];
	at = M[t] ? t : M[n] ? n : "en", rt = M[at], it = t;
}
function N() {
	return it;
}
function st() {
	return at;
}
function P(e, t) {
	let n = rt[e] ?? Ye[e] ?? e;
	return t ? n.replace(/\{(\w+)\}/g, (e, n) => n in t ? String(t[n]) : e) : n;
}
Object.keys(M), j("kkp-dialog", class extends E {
	static {
		this.properties = {
			open: { type: Boolean },
			heading: {},
			body: {},
			confirmLabel: {},
			destructive: { type: Boolean }
		};
	}
	static {
		this.styles = [A, o`
      dialog {
        width: min(440px, calc(100vw - 32px));
        padding: 28px;
        border: none;
        border-radius: 22px;
        background: var(--kk-surface);
        color: var(--kk-text);
        box-shadow: 0 24px 64px rgba(0, 0, 0, 0.45);
      }

      dialog::backdrop {
        background: rgba(0, 0, 0, 0.6);
        backdrop-filter: blur(4px);
      }

      h2 {
        margin: 0 0 12px;
        font-size: 22px;
        font-weight: 400;
      }

      p {
        margin: 0 0 28px;
        font-size: 16px;
        line-height: 1.5;
        color: var(--kk-text-2);
      }

      .actions {
        display: flex;
        gap: 12px;
        justify-content: flex-end;
      }
    `];
	}
	get dialog() {
		return this.renderRoot.querySelector("dialog");
	}
	updated() {
		let e = this.dialog;
		e && (this.open && !e.open && e.showModal(), !this.open && e.open && e.close());
	}
	answer(e) {
		this.dispatchEvent(new CustomEvent("kkp-answer", { detail: e }));
	}
	render() {
		return b`<dialog
      aria-labelledby="title"
      aria-describedby="body"
      @cancel=${(e) => {
			e.preventDefault(), this.answer(!1);
		}}
    >
      <h2 id="title">${this.heading}</h2>
      <p id="body">${this.body}</p>
      <div class="actions">
        <button class="pill" @click=${() => this.answer(!1)}>${P("dialog.cancel")}</button>
        <button class="pill ${this.destructive ? "danger" : "filled"}" @click=${() => this.answer(!0)}>
          ${this.confirmLabel}
        </button>
      </div>
    </dialog>`;
	}
}), j("kkp-header", class extends E {
	static {
		this.properties = {
			heading: {},
			back: { type: Boolean },
			more: { type: Boolean },
			moreLabel: {},
			menu: { type: Boolean }
		};
	}
	static {
		this.styles = [A, o`
      :host {
        display: block;
      }

      header {
        display: grid;
        grid-template-columns: 56px 1fr 56px;
        align-items: center;
        min-height: 72px;
        padding: 12px 0;
      }

      h1 {
        margin: 0;
        text-align: center;
        font-size: 20px;
        font-weight: 400;
        letter-spacing: 0.01em;
        overflow-wrap: anywhere;
      }

      .back svg {
        color: var(--kk-accent);
      }

      .end {
        justify-self: end;
      }
    `];
	}
	fire(e) {
		this.dispatchEvent(new CustomEvent(e, {
			bubbles: !0,
			composed: !0
		}));
	}
	focusHeading() {
		this.renderRoot.querySelector("h1")?.focus();
	}
	render() {
		return b`<header>
      ${this.back ? b`<button class="round back" aria-label=${P("nav.back")} @click=${() => this.fire("kkp-back")}>
          ${O("back")}
        </button>` : this.menu ? b`<button class="round" aria-label=${P("nav.menu")} @click=${() => this.fire("kkp-menu")}>
            ${O("menu")}
          </button>` : b`<span></span>`}
      <h1 tabindex="-1">${this.heading}</h1>
      ${this.more ? b`<button
            class="round end"
            aria-label=${this.moreLabel || P("nav.more")}
            @click=${() => this.fire("kkp-more")}
          >
            ${O("more")}
          </button>` : C}
    </header>`;
	}
});
var ct = o`
  :host {
    --kk-font: "Helvetica Now Text", "Helvetica Neue", Helvetica, Roboto, Arial,
      sans-serif;
    --kk-radius-card: 16px;
    --kk-radius-tile: 12px;
    --kk-radius-pill: 999px;
    --kk-temp-gradient: ${a("linear-gradient(90deg, #b4edfd 0%, #93daf3 10%, #72c8e9 20%, #52b0db 30%, #4b7cbb 40%, #3f4699 50%, #442a7c 60%, #713964 70%, #a13b45 80%, #c2382f 90%, #e43425 100%)")};
    --kk-cool: #43769b;
    --kk-warm: #b037fb;
    --kk-hot: #fd0002;
    --kk-ease: cubic-bezier(0.2, 0.7, 0.2, 1);
    /* Behind white text on a destructive button, in either theme. */
    --kk-danger-fill: #d62f2f;
  }

  :host([theme="dark"]) {
    color-scheme: dark;
    --kk-canvas: #000000;
    --kk-surface: #131517;
    --kk-surface-row: #121419;
    --kk-surface-raised: #171a1c;
    --kk-control: #202228;
    --kk-control-hover: #2a2d34;
    --kk-outlet: #353738;
    --kk-outlet-hover: #404244;
    --kk-divider: #222427;
    --kk-grid: #202329;
    --kk-nav: #202020;
    --kk-nav-active: #0a0a0a;
    --kk-sheet: #152226;
    --kk-scrim: rgba(0, 0, 0, 0.72);
    --kk-text: #f1f2f4;
    --kk-text-2: #9e9ea4;
    --kk-text-3: #84858b;
    --kk-accent: #00cdd4;
    --kk-accent-text: #00cccc;
    --kk-accent-fill: #2a7f81;
    --kk-accent-on: #ffffff;
    --kk-accent-soft: rgba(0, 205, 212, 0.14);
    --kk-accent-line: rgba(0, 205, 212, 0.45);
    --kk-success: #72ff66;
    --kk-warning: #ffb020;
    --kk-danger: #ff4d4d;
    --kk-destructive: #f26a2e;
    --kk-bar-top: #51d0d3;
    --kk-bar-bottom: #32707f;
    --kk-bar-track: #1f1f1f;
    --kk-art: #ffffff;
    --kk-thumb-edge: transparent;
    --kk-shadow: none;
  }

  :host([theme="light"]) {
    color-scheme: light;
    --kk-canvas: #f3f3f1;
    --kk-surface: #ffffff;
    --kk-surface-row: #ffffff;
    --kk-surface-raised: #ffffff;
    --kk-control: #ebebe8;
    --kk-control-hover: #e1e1dd;
    --kk-outlet: #e6e6e3;
    --kk-outlet-hover: #dcdcd8;
    --kk-divider: #e0e0dc;
    --kk-grid: #e6e6e2;
    --kk-nav: #ffffff;
    --kk-nav-active: #ecedea;
    --kk-sheet: #e7f1f1;
    --kk-scrim: rgba(17, 19, 22, 0.42);
    --kk-text: #111316;
    --kk-text-2: #5c5f66;
    --kk-text-3: #686c73;
    --kk-accent: #00838a;
    --kk-accent-text: #00747a;
    --kk-accent-fill: #007a7e;
    --kk-accent-on: #ffffff;
    --kk-accent-soft: rgba(0, 131, 138, 0.1);
    --kk-accent-line: rgba(0, 131, 138, 0.45);
    --kk-success: #1f9d3a;
    --kk-warning: #b26a00;
    --kk-danger: #c53628;
    --kk-destructive: #c2410c;
    --kk-bar-top: #2bb7bb;
    --kk-bar-bottom: #2a6a78;
    --kk-bar-track: #ececea;
    --kk-art: #111316;
    /* A white thumb on the pale cold end of the gradient needs an edge. */
    --kk-thumb-edge: #5c5f66;
    --kk-shadow: 0 1px 2px rgba(17, 19, 22, 0.06), 0 8px 24px rgba(17, 19, 22, 0.06);
  }
`;
// src/components/rows.ts
function F(e) {
	let t = b`
    <span class="text">
      <span class="label">${e.label}${e.tag ? b`<span class="tag">${e.tag}</span>` : C}</span>
      ${e.description ? b`<span class="sub">${e.description}</span>` : C}
    </span>
    ${e.value !== void 0 && e.value !== null ? b`<span class="value">${e.value}</span>` : C}
    ${e.trailing ?? C}
    ${e.chevron ? b`<span class="chev ${e.expanded ? "open" : ""}">${O("chevronRight")}</span>` : C}
  `;
	return e.onClick ? b`<button
    class="row ${e.destructive ? "destructive" : ""}"
    data-key=${e.key ?? C}
    ?disabled=${e.disabled}
    aria-expanded=${e.expanded === void 0 ? C : String(e.expanded)}
    @click=${e.onClick}
  >
    ${t}
  </button>` : b`<div class="row">${t}</div>`;
}
function I(e, t, n) {
	return b`<section class="group">
    ${e ? b`<h2 tabindex="-1">${e}</h2>` : C}
    ${n ? b`<p class="desc">${n}</p>` : C}
    <div class="rows">${t}</div>
  </section>`;
}
function lt(e) {
	let t = [...e.currentTarget.querySelectorAll("[role=\"radio\"]")], n = t.indexOf(e.target), r = t.length - 1, i = {
		ArrowDown: n === r ? 0 : n + 1,
		ArrowRight: n === r ? 0 : n + 1,
		ArrowUp: n === 0 ? r : n - 1,
		ArrowLeft: n === 0 ? r : n - 1,
		Home: 0,
		End: r
	};
	n < 0 || !(e.key in i) || (e.preventDefault(), t.forEach((t, n) => t.tabIndex = n === i[e.key] ? 0 : -1), t[i[e.key]].focus());
}
function ut(e, t, n) {
	return e || !n && t === 0 ? 0 : -1;
}
function L(e, t, n) {
	return b`<div class="banner ${e}" role=${e === "danger" ? "alert" : "status"}>
    ${O(e === "info" ? "info" : "alert")}
    <span>${t}</span>
    ${n ? b`<button class="banner-action" @click=${n.onClick}>${n.label}</button>` : C}
  </div>`;
}
var R = o`
  .group {
    margin: 0 0 8px;
  }

  .group h2 {
    margin: 30px 4px 12px;
    font-size: 22px;
    font-weight: 400;
  }

  .group .desc {
    margin: -4px 4px 14px;
    font-size: 15px;
    line-height: 1.45;
    color: var(--kk-text-2);
  }

  .rows {
    overflow: hidden;
    border-radius: var(--kk-radius-card);
    background: var(--kk-surface-row);
    box-shadow: var(--kk-shadow);
  }

  /* Inside the row: the group's rounded corners clip anything drawn beyond it. */
  button.row:focus-visible {
    outline-offset: -3px;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 14px;
    width: 100%;
    min-height: 64px;
    padding: 14px 20px;
    border-top: 1px solid var(--kk-divider);
    font-size: 18px;
    text-align: left;
    color: var(--kk-text);
  }

  .rows > .row:first-child,
  .rows > :first-child .row {
    border-top: none;
  }

  button.row {
    transition: background 160ms var(--kk-ease);
  }

  button.row:hover:not(:disabled) {
    background: var(--kk-control);
  }

  button.row:disabled {
    opacity: 0.5;
  }

  .row .text {
    flex: 1;
    min-width: 0;
  }

  /* Long words in other languages wrap rather than run out of the row. */
  .row .label {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    overflow-wrap: anywhere;
    hyphens: auto;
  }

  .row .sub {
    display: block;
    margin-top: 4px;
    font-size: 14px;
    line-height: 1.4;
    color: var(--kk-text-2);
  }

  .row .value {
    color: var(--kk-text-2);
    font-size: 17px;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  .row > svg {
    width: 24px;
    height: 24px;
    flex: none;
    color: var(--kk-text-2);
  }

  .row .chev {
    display: grid;
    color: var(--kk-text-2);
    transition: transform 200ms var(--kk-ease);
  }

  .row .chev svg {
    width: 22px;
    height: 22px;
  }

  .row .chev.open {
    transform: rotate(90deg);
  }

  .row.destructive .label {
    color: var(--kk-destructive);
  }

  .tag {
    padding: 2px 8px;
    border-radius: var(--kk-radius-pill);
    background: var(--kk-control);
    color: var(--kk-text-2);
    font-size: 12px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .editor {
    padding: 6px 20px 22px;
    background: var(--kk-surface-row);
  }

  .choices {
    display: grid;
  }

  .choice {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-height: 56px;
    padding: 0 4px;
    font-size: 17px;
    text-align: left;
    border-top: 1px solid var(--kk-divider);
  }

  .choice:first-child {
    border-top: none;
  }

  .radio {
    display: grid;
    place-items: center;
    width: 26px;
    height: 26px;
    border-radius: 50%;
    border: 2px solid var(--kk-text-2);
    flex: none;
  }

  .choice[aria-checked="true"] .radio {
    border-color: var(--kk-accent);
  }

  .choice[aria-checked="true"] .radio::after {
    content: "";
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: var(--kk-accent);
  }

  .editor-actions {
    display: flex;
    justify-content: flex-end;
    gap: 12px;
    margin-top: 18px;
  }

  .dot {
    display: inline-block;
    width: 10px;
    height: 10px;
    margin-right: 8px;
    border-radius: 50%;
    background: var(--kk-text-3);
    vertical-align: 1px;
  }

  .dot.good {
    background: var(--kk-success);
  }

  .dot.warn {
    background: var(--kk-warning);
  }

  .dot.bad {
    background: var(--kk-danger);
  }
`, z = o`
  .banner {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 16px;
    border-radius: 14px;
    background: var(--kk-surface);
    font-size: 15px;
    line-height: 1.4;
  }

  .banner svg {
    width: 22px;
    height: 22px;
    flex: none;
  }

  .banner span {
    flex: 1;
  }

  .banner.info svg {
    color: var(--kk-text);
  }

  .banner.warning svg {
    color: var(--kk-warning);
  }

  .banner.danger {
    background: color-mix(in srgb, var(--kk-danger) 14%, var(--kk-surface));
  }

  .banner.danger svg {
    color: var(--kk-danger);
  }

  .banner-action {
    padding: 8px 16px;
    border-radius: var(--kk-radius-pill);
    border: 1.5px solid currentColor;
    font-size: 15px;
  }
`;
// src/util/entity.ts
function B(e, t, n) {
	let r = t?.roles[n];
	return r ? e?.states[r] : void 0;
}
function V(e) {
	return !!e && e.state !== "unavailable";
}
function H(e) {
	return e?.state === "on";
}
function U(e) {
	if (!V(e) || e.state === "unknown") return null;
	let t = Number(e.state);
	return Number.isFinite(t) ? t : null;
}
function W(e, t, n = t.state) {
	if (e?.formatEntityState) return e.formatEntityState(t, n);
	let r = t.attributes.unit_of_measurement;
	return r && n === t.state ? `${n} ${r}` : n;
}
function dt(e, t, n, r = t.attributes[n]) {
	return e?.formatEntityAttributeValue?.(t, n, r) ?? String(r ?? "");
}
function G(e, t) {
	return e.untested.includes(t);
}
function ft(e) {
	let t = e?.attributes.options;
	return Array.isArray(t) ? t.filter((e) => e !== "Off").map(String) : [];
}
// src/context.ts
function K(e) {
	return e && typeof e == "object" && "message" in e ? String(e.message) : String(e);
}
var q = class extends E {
	constructor(...e) {
		super(...e), this.hadFocus = !1;
	}
	static {
		this.properties = {
			hass: { attribute: !1 },
			device: { attribute: !1 },
			live: { attribute: !1 },
			ctx: { attribute: !1 }
		};
	}
	willUpdate(e) {
		super.willUpdate(e), this.hadFocus = this.matches(":focus-within");
	}
	updated(e) {
		super.updated(e), this.hadFocus && !this.matches(":focus-within") && this.focusFallback()?.focus();
	}
	focusFallback() {
		return null;
	}
	statusRegion(e) {
		return b`<div class="visually-hidden" role="status">${e}</div>`;
	}
	entity(e) {
		return B(this.hass, this.device, e);
	}
	has(e) {
		return !!this.entity(e);
	}
	isOn(e) {
		return H(this.entity(e));
	}
	number(e) {
		return U(this.entity(e));
	}
	entityId(e) {
		return this.device.roles[e];
	}
	async call(e, t, n = {}) {
		try {
			return await this.ctx.api.service(e, t, n), !0;
		} catch (e) {
			return this.ctx.toast(K(e), "error"), !1;
		}
	}
	async queued(e, t, n, r) {
		try {
			return await this.ctx.api.queue(this.device.id).enqueue(e, () => this.ctx.api.service(t, n, r)), !0;
		} catch (e) {
			return e instanceof Ve || this.ctx.toast(K(e), "error"), !1;
		}
	}
	async stopNow(e, t, n) {
		try {
			await this.ctx.api.queue(this.device.id).stop(() => this.ctx.api.service(e, t, n));
		} catch (e) {
			this.ctx.toast(K(e), "error");
		}
	}
	roleService(e, t, n, r = {}) {
		let i = this.entityId(e);
		return i ? this.call(t, n, {
			entity_id: i,
			...r
		}) : Promise.resolve(!1);
	}
};
// src/util/format.ts
function pt(e) {
	return new Intl.NumberFormat(N(), e);
}
function J(e, t = 0) {
	return pt({
		maximumFractionDigits: t,
		minimumFractionDigits: 0
	}).format(e);
}
function Y(e, t) {
	return e == null || Number.isNaN(e) ? "—" : `${J(e, +(t === "°C"))}${t}`;
}
function mt(e, t) {
	return e == null || Number.isNaN(e) ? "—" : `${J(e, Math.abs(e) < 10 ? 2 : +(Math.abs(e) < 100))} ${t}`;
}
function ht(e) {
	if (e == null || Number.isNaN(e)) return "—";
	let t = Math.round(e);
	return t < 60 ? P("unit.minutes_short", { count: t }) : P("unit.hours_minutes", {
		hours: Math.floor(t / 60),
		minutes: t % 60
	});
}
function gt(e) {
	if (e == null || e < 0) return "—";
	let t = Math.round(e), n = Math.floor(t / 3600), r = Math.floor(t % 3600 / 60), i = t % 60, a = n ? String(r).padStart(2, "0") : String(r);
	return `${n ? `${n}:` : ""}${a}:${String(i).padStart(2, "0")}`;
}
function _t(e) {
	let t = /^(\d{4})-(\d{2})-(\d{2})$/.exec(e);
	return t ? new Date(Number(t[1]), Number(t[2]) - 1, Number(t[3])) : new Date(e);
}
function vt(e) {
	if (!e) return "—";
	let t = _t(e);
	return Number.isNaN(t.getTime()) ? e : new Intl.DateTimeFormat(N(), { dateStyle: "medium" }).format(t);
}
function yt(e) {
	if (!e) return "—";
	let t = new Date(e);
	return Number.isNaN(t.getTime()) ? e : new Intl.DateTimeFormat(N(), {
		dateStyle: "medium",
		timeStyle: "short"
	}).format(t);
}
function bt(e, t = Date.now()) {
	if (!e) return "—";
	let n = new Date(e).getTime();
	if (Number.isNaN(n)) return "—";
	let r = Math.round((n - t) / 1e3), i = new Intl.RelativeTimeFormat(N(), { numeric: "auto" }), a = Math.abs(r);
	return a < 60 ? i.format(r, "second") : a < 3600 ? i.format(Math.round(r / 60), "minute") : a < 86400 ? i.format(Math.round(r / 3600), "hour") : i.format(Math.round(r / 86400), "day");
}
function xt(e) {
	let [t, n] = e.split(":").map(Number);
	return !Number.isInteger(t) || !Number.isInteger(n) ? e : new Intl.DateTimeFormat(N(), { timeStyle: "short" }).format(new Date(2024, 0, 1, t, n));
}
var St = [
	"mon",
	"tue",
	"wed",
	"thu",
	"fri",
	"sat",
	"sun"
];
function Ct(e) {
	let t = new Intl.DateTimeFormat(N(), { weekday: "short" });
	return St.flatMap((n, r) => e.includes(n) ? [t.format(new Date(2024, 0, 1 + r))] : []).join(", ");
}
// src/views/about.ts
var wt = {
	gateway: "about.gateway",
	valve: "about.valves",
	interface: "about.interfaces"
}, Tt = {
	interface: "about.firmware.interface",
	valve: "about.firmware.valve",
	second_valve: "about.firmware.second_valve",
	gateway: "about.firmware.gateway",
	controller: "about.firmware.controller",
	application: "about.firmware.application",
	bootloader: "about.firmware.bootloader",
	os: "about.firmware.os",
	certificate: "about.firmware.certificate",
	service_pack: "about.firmware.service_pack"
};
j("kkp-about", class extends q {
	static {
		this.properties = {
			...q.properties,
			details: { state: !0 },
			open: { state: !0 }
		};
	}
	constructor() {
		super(), this.details = null, this.open = null;
	}
	static {
		this.styles = [
			A,
			R,
			o`
      :host {
        display: block;
        max-width: 760px;
        padding-bottom: 48px;
      }

      .part {
        padding: 6px 4px 6px;
        font-size: 16px;
      }

      .part dl {
        display: grid;
        grid-template-columns: max-content 1fr;
        gap: 10px 24px;
        margin: 0;
      }

      .part dt {
        color: var(--kk-text-2);
      }

      .part dd {
        margin: 0;
        text-align: right;
        font-variant-numeric: tabular-nums;
        overflow-wrap: anywhere;
      }

      .part + .part {
        border-top: 1px solid var(--kk-divider);
        padding-top: 14px;
        margin-top: 8px;
      }

      .part h3 {
        margin: 0 0 10px;
        font-size: 16px;
        font-weight: 500;
      }

      .note {
        font-size: 14px;
        color: var(--kk-text-2);
      }
    `
		];
	}
	async firstUpdated() {
		try {
			this.details = await this.ctx.api.deviceDetails(this.device.id);
		} catch (e) {
			this.ctx.toast(K(e), "error");
		}
	}
	firmwareValue() {
		let e = this.entity("firmware");
		return e ? e.state === "on" ? b`<span class="dot warn"></span>${P("about.update_available", { version: e.attributes.latest_version ?? "" })}` : b`<span class="dot good"></span>${P("about.up_to_date")}` : this.details?.firmware[0]?.version ?? "—";
	}
	renderPart(e) {
		let t = [
			["about.model", e.model],
			["about.serial", e.serial],
			["about.firmware", e.firmware],
			["about.install_date", e.install_date ? vt(e.install_date) : null],
			["about.status", e.status]
		];
		return b`<div class="part">
      ${e.name ? b`<h3>${e.name}</h3>` : C}
      <dl>
        ${t.filter(([, e]) => e).map(([e, t]) => b`<dt>${P(e)}</dt><dd>${t}</dd>`)}
      </dl>
    </div>`;
	}
	renderParts() {
		let e = this.details?.parts ?? [], t = [...new Set(e.map((e) => e.kind))];
		return t.length ? I(P("about.technical"), t.map((t) => {
			let n = this.open === t;
			return b`${F({
				label: P(wt[t] ?? "about.technical"),
				chevron: !0,
				expanded: n,
				onClick: () => this.open = n ? null : t
			})}
          ${n ? b`<div class="editor">${e.filter((e) => e.kind === t).map((e) => this.renderPart(e))}</div>` : C}`;
		})) : C;
	}
	render() {
		let e = this.details, t = P("about.unknown"), n = [e?.model_name ?? this.device.model_name, e?.model ?? this.device.model].filter(Boolean).join(" · "), r = e?.firmware.filter((e) => e.version) ?? [];
		return b`
      ${I(null, b`
          ${F({ label: this.device.name })}
          ${F({
			label: P("about.model"),
			value: n || t
		})}
          ${F({
			label: P("about.serial"),
			value: e?.serial ?? t
		})}
          ${F({
			label: P("about.install_date"),
			value: e?.install_date ? vt(e.install_date) : t
		})}
          ${e?.registered ? F({
			label: P("about.registered"),
			value: vt(e.registered)
		}) : C}
          ${e?.hardware ? F({
			label: P("about.hardware"),
			value: e.hardware
		}) : C}
          ${F({
			label: P("about.firmware"),
			value: this.firmwareValue(),
			description: this.entity("firmware")?.state === "on" ? P("about.update_in_app") : void 0
		})}
          ${r.map((e) => F({
			label: Tt[e.key] ? P(Tt[e.key]) : e.key,
			value: e.version
		}))}
          ${F({
			label: P("settings.diagnostics"),
			chevron: !0,
			onClick: () => this.ctx.navigate(`/d/${this.device.id}/diagnostics`)
		})}
        `)}
      ${this.renderParts()}
      ${this.device.kind === "faucet" ? I(P("about.leak_history"), e?.leak_history.length ? e.leak_history.map((e) => F({ label: yt(e) })) : F({ label: P("about.no_leaks") })) : C}
    `;
	}
});
// src/components/running-hero.ts
var Et = [
	[0, [
		67,
		118,
		155
	]],
	[.35, [
		0,
		205,
		212
	]],
	[.7, [
		176,
		55,
		251
	]],
	[1, [
		253,
		0,
		2
	]]
];
function Dt(e, t) {
	if (e === null) return .5;
	let [n, r] = t === "°C" ? [32, 46] : [90, 115];
	return Math.min(1, Math.max(0, (e - n) / (r - n)));
}
function Ot(e) {
	let t = Math.min(1, Math.max(0, e));
	for (let e = 1; e < Et.length; e++) {
		let [n, r] = Et[e], [i, a] = Et[e - 1];
		if (t <= n) {
			let e = (t - i) / (n - i);
			return `rgb(${a.map((t, n) => Math.round(t + (r[n] - t) * e)).join(" ")})`;
		}
	}
	return "rgb(253 0 2)";
}
var kt = Array.from({ length: 16 }, (e, t) => ({
	left: t * 37 % 100,
	size: 6 + t * 7 % 16,
	delay: -(t * .61 % 4.84),
	drift: (t % 5 - 2) * 6
}));
j("kkp-running", class extends E {
	static {
		this.shadowRootOptions = {
			...E.shadowRootOptions,
			delegatesFocus: !0
		};
	}
	static {
		this.properties = {
			phase: {},
			temperature: { type: Number },
			setpoint: { type: Number },
			unit: {},
			tint: { type: Number },
			atTemperature: { type: Boolean },
			secondsRemaining: { type: Number },
			totalSeconds: { type: Number },
			flowingFor: { type: Number }
		};
	}
	static {
		this.styles = [A, o`
      :host {
        display: block;
      }

      .hero {
        position: relative;
        overflow: hidden;
        isolation: isolate;
        border-radius: 22px;
        background: #040607;
        color: #f1f2f4;
        min-height: 300px;
        box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.06) inset;
      }

      .glow {
        position: absolute;
        inset: -35%;
        z-index: -2;
        background: radial-gradient(42% 46% at 30% 68%, var(--tint) 0%, transparent 70%),
          radial-gradient(36% 40% at 74% 30%, rgba(0, 205, 212, 0.55) 0%, transparent 72%),
          radial-gradient(30% 30% at 60% 90%, var(--tint) 0%, transparent 70%);
        filter: blur(24px) saturate(1.15);
        opacity: 0.8;
        animation: kk-drift 8s ease-in-out infinite alternate;
      }

      .paused .glow {
        opacity: 0.45;
        animation-duration: 12s;
      }

      .bubbles {
        position: absolute;
        inset: 0;
        z-index: -1;
        pointer-events: none;
      }

      .bubbles span {
        position: absolute;
        bottom: -30px;
        border-radius: 50%;
        border: 1px solid rgba(255, 255, 255, 0.32);
        background: radial-gradient(
          circle at 32% 30%,
          rgba(255, 255, 255, 0.42),
          rgba(255, 255, 255, 0.04) 62%
        );
        animation: kk-rise 4.84s linear infinite;
      }

      .paused .bubbles span {
        animation-play-state: paused;
        opacity: 0.25;
      }

      .content {
        position: relative;
        display: grid;
        gap: 22px;
        padding: 22px 24px 24px;
      }

      .top {
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 16px;
        letter-spacing: 0.02em;
      }

      .dot {
        width: 9px;
        height: 9px;
        border-radius: 50%;
        background: #00cdd4;
        box-shadow: 0 0 0 0 rgba(0, 205, 212, 0.6);
        animation: kk-beacon 2s infinite;
      }

      .paused .dot {
        background: #ffb020;
        animation: none;
      }

      .main {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
      }

      .temp {
        display: grid;
        gap: 6px;
      }

      .big {
        font-size: 84px;
        font-weight: 200;
        line-height: 0.95;
        letter-spacing: -0.03em;
        font-variant-numeric: tabular-nums;
      }

      .big small {
        font-size: 30px;
        font-weight: 300;
        letter-spacing: 0;
        vertical-align: top;
        margin-left: 4px;
      }

      .sub {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 6px 12px;
        font-size: 15px;
        color: rgba(241, 242, 244, 0.72);
      }

      .sub .ok {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        color: #72ff66;
      }

      .sub .ok svg {
        width: 16px;
        height: 16px;
      }

      .ring {
        position: relative;
        width: 112px;
        height: 112px;
        flex: none;
      }

      .ring svg {
        width: 100%;
        height: 100%;
        transform: rotate(-90deg);
      }

      .ring .track {
        stroke: rgba(255, 255, 255, 0.12);
      }

      .ring .progress {
        stroke: #f1f2f4;
        transition: stroke-dashoffset 900ms linear;
      }

      .ring .label {
        position: absolute;
        inset: 0;
        display: grid;
        place-content: center;
        text-align: center;
      }

      .ring .label strong {
        font-size: 24px;
        font-weight: 300;
        font-variant-numeric: tabular-nums;
      }

      .ring .label span {
        font-size: 12px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: rgba(241, 242, 244, 0.6);
      }

      .actions {
        display: flex;
        gap: 12px;
      }

      .actions .pill {
        flex: 1;
        min-height: 52px;
        border-color: rgba(241, 242, 244, 0.8);
        color: #f1f2f4;
        font-size: 18px;
        backdrop-filter: blur(8px);
        background: rgba(0, 0, 0, 0.18);
      }

      .actions .pill.filled {
        background: var(--kk-accent-fill);
        border-color: transparent;
      }

      .paused-note {
        margin: -8px 0 0;
        font-size: 15px;
        color: rgba(241, 242, 244, 0.72);
      }

      @keyframes kk-drift {
        from {
          transform: translate3d(-3%, 2%, 0) rotate(0deg) scale(1);
        }
        to {
          transform: translate3d(4%, -3%, 0) rotate(8deg) scale(1.08);
        }
      }

      @keyframes kk-rise {
        0% {
          transform: translate3d(0, 0, 0) scale(0.55);
          opacity: 0;
        }
        12% {
          opacity: 0.9;
        }
        100% {
          transform: translate3d(var(--drift), -360px, 0) scale(1.15);
          opacity: 0;
        }
      }

      @keyframes kk-beacon {
        0% {
          box-shadow: 0 0 0 0 rgba(0, 205, 212, 0.6);
        }
        80%,
        100% {
          box-shadow: 0 0 0 10px rgba(0, 205, 212, 0);
        }
      }

      @media (max-width: 420px) {
        .big {
          font-size: 68px;
        }
        .ring {
          width: 96px;
          height: 96px;
        }
      }
    `];
	}
	fire(e) {
		this.dispatchEvent(new CustomEvent(e));
	}
	renderRing() {
		if (this.secondsRemaining === null || this.secondsRemaining === void 0) return C;
		let e = Math.max(this.totalSeconds ?? this.secondsRemaining, this.secondsRemaining, 1), t = 2 * Math.PI * 46;
		return b`<div class="ring">
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <circle class="track" cx="50" cy="50" r="46" fill="none" stroke-width="4" />
        <circle
          class="progress"
          cx="50"
          cy="50"
          r="46"
          fill="none"
          stroke-width="4"
          stroke-linecap="round"
          stroke-dasharray=${t}
          stroke-dashoffset=${t * (1 - this.secondsRemaining / e)}
        />
      </svg>
      <div class="label">
        <strong>${gt(this.secondsRemaining)}</strong>
        <span>${P("shower.time_left")}</span>
      </div>
    </div>`;
	}
	render() {
		let e = this.phase === "warming" ? P("shower.warming_title") : this.phase === "paused" ? P("shower.paused_title") : P("shower.running_title"), t = this.temperature ?? this.setpoint, n = t == null ? "—" : Y(t, "");
		return b`<section class="hero ${this.phase}" style="--tint: ${Ot(this.tint ?? .5)}">
      <div class="glow"></div>
      <div class="bubbles" aria-hidden="true">
        ${kt.map((e) => b`<span
              style="left:${e.left}%;width:${e.size}px;height:${e.size}px;animation-delay:${e.delay}s;--drift:${e.drift}px"
            ></span>`)}
      </div>
      <div class="content">
        <div class="top"><span class="dot"></span><span>${e}</span></div>
        <div class="main">
          <div class="temp">
            <div class="big">${n}<small>${this.unit}</small></div>
            <div class="sub">
              ${this.atTemperature ? b`<span class="ok">${O("checkCircle")}${P("shower.at_temperature")}</span>` : b`<span>${P("shower.heating")}</span>`}
              ${this.setpoint !== null && this.setpoint !== void 0 && this.setpoint !== t ? b`<span>${P("shower.set_to", { temperature: Y(this.setpoint, this.unit) })}</span>` : C}
            </div>
          </div>
          ${this.renderRing()}
        </div>
        ${this.phase === "paused" ? b`<p class="paused-note">${P("shower.paused_body")}</p>` : C}
        <div class="actions">
          ${this.phase === "paused" ? b`<button class="pill filled" @click=${() => this.fire("kkp-resume")}>
                ${O("play")}${P("shower.resume")}
              </button>` : C}
          <button class="pill" @click=${() => this.fire("kkp-stop")}>
            ${O("stop")}${P("shower.stop")}
          </button>
        </div>
      </div>
    </section>`;
	}
});
function X(e, t) {
	let n = B(e, t, "status");
	return V(n) ? n.state === "Water Running" ? "running" : n.state === "Warming Up" ? "warming" : n.state === "Paused" ? "paused" : "idle" : "offline";
}
function At(e) {
	switch (e) {
		case "warming": return `${P("shower.warming_title")}. ${P("shower.locked_warming")}`;
		case "running": return P("shower.running_title");
		case "paused": return `${P("shower.paused_title")}. ${P("shower.paused_body")}`;
		case "offline": return P("status.offline");
		default: return P("status.idle");
	}
}
function jt(e, t) {
	let n = H(B(e, t, "problem"));
	if (t.kind === "faucet") {
		if (H(B(e, t, "leak"))) return {
			text: P("status.leak"),
			tone: "alert",
			running: !1
		};
		let n = B(e, t, "water") ?? B(e, t, "status");
		return V(n) ? H(B(e, t, "dispensing")) ? {
			text: P("status.dispensing"),
			tone: "active",
			running: !0
		} : H(B(e, t, "status")) || H(n) ? {
			text: P("status.water_on"),
			tone: "active",
			running: !0
		} : {
			text: P("status.off"),
			tone: "idle",
			running: !1
		} : {
			text: P("status.offline"),
			tone: "offline",
			running: !1
		};
	}
	if (t.kind === "valve" || t.kind === "controller") {
		let r = X(e, t);
		if (r === "offline") return {
			text: P("status.offline"),
			tone: "offline",
			running: !1
		};
		let i = B(e, t, "temperature:1"), a = String(i?.attributes.unit_of_measurement ?? t.units.temperature), o = Y(U(i), a), s = B(e, t, "status")?.attributes.seconds_remaining;
		if (r === "running") {
			let e = [P("status.running"), o];
			return typeof s == "number" && e.push(P("status.minutes_left", { time: gt(s) })), {
				text: e.join(" · "),
				tone: "active",
				running: !0
			};
		}
		return r === "warming" ? {
			text: `${P("status.warming")} · ${o}`,
			tone: "active",
			running: !0
		} : r === "paused" ? {
			text: P("status.paused"),
			tone: "active",
			running: !1
		} : n ? {
			text: P("status.problem"),
			tone: "alert",
			running: !1
		} : H(B(e, t, "steam_state")) ? {
			text: P("controller.steam"),
			tone: "active",
			running: !0
		} : {
			text: P("status.idle"),
			tone: "idle",
			running: !1
		};
	}
	if (t.kind === "smart_fan") {
		let n = B(e, t, "fan_state"), r = B(e, t, "connected");
		if (!V(n) || r?.state === "off") return {
			text: P("status.offline"),
			tone: "offline",
			running: !1
		};
		let i = n.state !== "off" && n.state !== "unknown", a = i ? [P("smart_fan.fan_on"), W(e, n)] : [];
		return H(B(e, t, "light_state")) && a.push(P("smart_fan.light_on")), a.length ? {
			text: a.join(" · "),
			tone: "active",
			running: i
		} : {
			text: P("status.off"),
			tone: "idle",
			running: !1
		};
	}
	if (t.kind === "toilet") {
		let n = B(e, t, "activity"), r = B(e, t, "connected");
		if (!V(n) || r?.state === "off") return {
			text: P("status.offline"),
			tone: "offline",
			running: !1
		};
		let i = B(e, t, "last_error")?.state;
		return i && i !== "unknown" && i !== "unavailable" ? {
			text: P("status.problem"),
			tone: "alert",
			running: !1
		} : n.state === "idle" ? H(B(e, t, "cleaning")) ? {
			text: P("toilet.cleaning"),
			tone: "active",
			running: !0
		} : H(B(e, t, "occupied")) ? {
			text: P("toilet.occupied"),
			tone: "idle",
			running: !1
		} : {
			text: P("status.ready"),
			tone: "idle",
			running: !1
		} : {
			text: W(e, n),
			tone: "active",
			running: !0
		};
	}
	if (t.kind === "evocycle") {
		let n = B(e, t, "evo_activity");
		return !V(n) || B(e, t, "connected")?.state === "off" ? {
			text: P("status.offline"),
			tone: "offline",
			running: !1
		} : H(B(e, t, "evo_problem")) ? {
			text: P("status.problem"),
			tone: "alert",
			running: !1
		} : n.state === "idle" ? {
			text: P("status.ready"),
			tone: "idle",
			running: !1
		} : {
			text: W(e, n),
			tone: "active",
			running: !0
		};
	}
	if (t.kind === "ice_bath") {
		let n = B(e, t, "sessions_this_week");
		if (!V(n)) return {
			text: P("status.offline"),
			tone: "offline",
			running: !1
		};
		let r = U(n) ?? 0, i = n.attributes.target;
		return {
			text: typeof i == "number" ? P("ice_bath.week_of", {
				done: r,
				target: i
			}) : P("ice_bath.week", { done: r }),
			tone: "idle",
			running: !1
		};
	}
	return n ? {
		text: P("status.problem"),
		tone: "alert",
		running: !1
	} : {
		text: P("status.ready"),
		tone: "idle",
		running: !1
	};
}
j("kkp-scenes", class extends q {
	static {
		this.properties = {
			...q.properties,
			role: {},
			compact: { type: Boolean }
		};
	}
	static {
		this.styles = [A, o`
      :host {
        display: block;
      }

      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 200px), 1fr));
        gap: 12px;
      }

      :host([compact]) .grid {
        grid-template-columns: 1fr;
        gap: 8px;
      }

      .scene {
        position: relative;
        display: grid;
        grid-template-columns: auto 1fr auto;
        align-items: center;
        gap: 14px;
        min-height: 76px;
        padding: 16px 18px;
        border-radius: var(--kk-radius-tile);
        background: var(--kk-surface);
        box-shadow: var(--kk-shadow);
        text-align: left;
        overflow: hidden;
        transition: transform 180ms var(--kk-ease), background 200ms var(--kk-ease);
      }

      .scene:not(.active):hover:not(:disabled) {
        transform: translateY(-1px);
        background: var(--kk-control);
      }

      .scene.active {
        background: var(--kk-accent-fill);
        color: var(--kk-accent-on);
      }

      .scene-icon {
        display: grid;
        place-items: center;
        width: 44px;
        height: 44px;
        border-radius: 50%;
        background: var(--kk-control);
        color: var(--kk-accent);
      }

      .active .scene-icon {
        background: rgba(255, 255, 255, 0.2);
        color: #fff;
      }

      .scene-icon svg {
        width: 22px;
        height: 22px;
      }

      .scene-name {
        font-size: 17px;
        line-height: 1.3;
      }

      .scene-action {
        padding: 7px 16px;
        border-radius: var(--kk-radius-pill);
        border: 1.5px solid var(--kk-text-2);
        font-size: 14px;
      }

      .active .scene-action {
        border-color: rgba(255, 255, 255, 0.85);
      }

      .empty {
        display: grid;
        justify-items: center;
        gap: 8px;
        padding: 48px 24px;
        text-align: center;
        color: var(--kk-text-2);
        border-radius: var(--kk-radius-card);
        background: var(--kk-surface);
      }

      .empty svg {
        width: 36px;
        height: 36px;
        color: var(--kk-text-3);
      }

      .empty strong {
        font-weight: 400;
        color: var(--kk-text);
        font-size: 18px;
      }
    `];
	}
	choose(e, t) {
		let n = this.entityId(this.role);
		n && this.queued("scene", "select", "select_option", {
			entity_id: n,
			option: t ? "Off" : e
		});
	}
	render() {
		let e = this.entity(this.role), t = ft(e), n = this.role === "favorite";
		return t.length ? b`<div class="grid">
      ${t.map((t) => {
			let r = e?.state === t;
			return b`<button
          class="scene ${r ? "active" : ""}"
          aria-pressed=${String(r)}
          @click=${() => this.choose(t, r)}
        >
          <span class="scene-icon">${O(n ? r ? "favoriteFilled" : "favorite" : "experiences")}</span>
          <span class="scene-name">${t}</span>
          <span class="scene-action">${P(r ? "scenes.running" : "scenes.start")}</span>
        </button>`;
		})}
    </div>${C}` : b`<div class="empty">
        ${O(n ? "favorite" : "experiences")}
        <strong>${P(n ? "scenes.empty_favorites" : "scenes.empty_experiences")}</strong>
        <span>${P(n ? "scenes.empty_favorites_body" : "scenes.empty_experiences_body")}</span>
      </div>`;
	}
}), j("kkp-controller", class extends q {
	static {
		this.properties = {
			...q.properties,
			tab: {},
			wide: { type: Boolean }
		};
	}
	static {
		this.styles = [
			A,
			z,
			o`
      :host {
        display: block;
      }

      .layout {
        display: grid;
        gap: 18px;
        align-items: start;
      }

      :host([wide]) .layout {
        grid-template-columns: minmax(0, 1.25fr) minmax(320px, 1fr);
        gap: 28px;
      }

      .col {
        display: grid;
        gap: 18px;
      }

      .simple-start {
        display: flex;
        align-items: center;
        gap: 20px;
        width: 100%;
        min-height: 84px;
        padding: 16px 24px;
        border-radius: var(--kk-radius-card);
        border: 1.5px solid var(--kk-accent-line);
        background: var(--kk-surface-raised);
        text-align: left;
        font-size: 26px;
      }

      .simple-start svg {
        width: 34px;
        height: 34px;
        color: var(--kk-accent-fill);
      }

      .panel {
        display: grid;
        gap: 18px;
        padding: 22px 24px;
      }

      .panel-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
      }

      .zones {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 12px;
      }

      .zone {
        display: grid;
        gap: 10px;
        padding: 16px;
        border-radius: var(--kk-radius-tile);
        background: var(--kk-control);
      }

      .zone strong {
        font-size: 30px;
        font-weight: 300;
      }

      .dots {
        display: flex;
        gap: 6px;
      }

      .dots i {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: var(--kk-outlet);
        /* An edge, so an off dot shows against the tile by more than its fill. */
        box-shadow: inset 0 0 0 1.5px var(--kk-text-2);
      }

      .dots i.on {
        background: var(--kk-accent);
        box-shadow: none;
      }

      .accessories {
        display: flex;
        gap: 12px;
      }

      .acc {
        display: grid;
        justify-items: center;
        gap: 8px;
        flex: 1;
        padding: 14px 8px;
        border-radius: var(--kk-radius-tile);
        background: var(--kk-control);
        color: var(--kk-text-2);
        font-size: 14px;
      }

      .acc svg {
        width: 26px;
        height: 26px;
      }

      .acc.on {
        background: var(--kk-accent-soft);
        color: var(--kk-accent-text);
        font-weight: 500;
      }

      .steam-meta {
        font-size: 15px;
        color: var(--kk-text-2);
      }

      .note {
        margin: 0;
        font-size: 14px;
        color: var(--kk-text-3);
      }

      .side h2 {
        margin: 6px 4px 2px;
        font-size: 18px;
        font-weight: 400;
        color: var(--kk-text-2);
      }
    `
		];
	}
	focusFallback() {
		return this.renderRoot.querySelector("kkp-running, .simple-start");
	}
	toggle(e) {
		let t = this.entityId(e);
		t && (this.isOn(e) ? this.stopNow("switch", "turn_off", { entity_id: t }) : this.queued(e, "switch", "turn_on", { entity_id: t }));
	}
	renderTop() {
		let e = X(this.hass, this.device);
		if (e === "idle" || e === "offline") return b`<button
        class="simple-start"
        ?disabled=${e === "offline" || !this.has("shower")}
        @click=${() => this.toggle("shower")}
      >
        ${O("play")}<span>${P("shower.simple_start")}</span>
      </button>`;
		let t = this.entity("temperature:1"), n = U(t), r = String(t?.attributes.unit_of_measurement ?? this.device.units.temperature);
		return b`<kkp-running
      .phase=${e === "paused" ? "running" : e}
      .temperature=${n}
      .setpoint=${n}
      .unit=${r}
      .tint=${Dt(n, r)}
      .atTemperature=${!1}
      .secondsRemaining=${null}
      @kkp-stop=${() => this.toggle("shower")}
    ></kkp-running>`;
	}
	renderSteam() {
		if (!this.has("steam")) return C;
		let e = this.isOn("steam"), t = this.isOn("shower"), n = this.entity("steam_state")?.attributes.temperature;
		return b`<section class="card panel">
      <div class="panel-head">
        <h2 class="section-label">${P("controller.steam")}</h2>
        <button class="pill ${e ? "" : "filled"}" ?disabled=${!e && t} @click=${() => this.toggle("steam")}>
          ${P(e ? "controller.steam_stop" : "controller.steam_start")}
        </button>
      </div>
      ${e && n !== void 0 ? b`<div class="steam-meta">${Y(Number(n), "°F")}</div>` : C}
      ${!e && t ? b`<p class="note">${P("controller.steam_blocked")}</p>` : C}
    </section>`;
	}
	renderStatus() {
		let e = [
			[
				"music",
				"music",
				P("controller.music")
			],
			[
				"light",
				"light",
				P("controller.light")
			],
			[
				"steam_state",
				"cloud",
				P("controller.steam")
			]
		];
		return b`<section class="card panel">
      <h2 class="section-label">${P("controller.status")}</h2>
      <div class="zones">
        ${this.device.zones.map((e) => b`<div class="zone">
            <span class="muted">${P("shower.zone", { zone: e.zone })}</span>
            <strong>${Y(this.number(`temperature:${e.zone}`), String(this.entity(`temperature:${e.zone}`)?.attributes.unit_of_measurement ?? this.device.units.temperature))}</strong>
            <div class="dots">
              ${e.outlets.map((t) => {
			let n = this.isOn(`outlet:${e.zone}:${t.position}`);
			return b`<i
                  class=${n ? "on" : ""}
                  role="img"
                  aria-label=${`${t.name}: ${P(n ? "status.on" : "status.off")}`}
                  title=${t.name}
                ></i>`;
		})}
            </div>
          </div>`)}
      </div>
      <div class="accessories">
        ${e.filter(([e]) => this.has(e)).map(([e, t, n]) => b`<div class="acc ${this.isOn(e) ? "on" : ""}">
              ${O(t)}<span>${n}</span>
              <span class="visually-hidden">${this.isOn(e) ? P("status.on") : P("status.off")}</span>
            </div>`)}
      </div>
      <p class="note">${P("controller.read_only")}</p>
    </section>`;
	}
	scenes(e, t = !1) {
		return b`<kkp-scenes
      .hass=${this.hass}
      .device=${this.device}
      .live=${this.live}
      .ctx=${this.ctx}
      .role=${e}
      ?compact=${t}
    ></kkp-scenes>`;
	}
	render() {
		return b`${this.statusRegion(At(X(this.hass, this.device)))}${this.renderTab()}`;
	}
	renderTab() {
		let e = b`
      ${this.isOn("problem") ? L("warning", this.entity("problem")?.attributes.active_errors?.[0]?.title ?? P("status.problem")) : C}
      ${this.renderTop()} ${this.renderSteam()} ${this.renderStatus()}
    `;
		return this.wide ? b`<div class="layout">
        <div class="col">${e}</div>
        <div class="col side">
          <h2>${P("nav.favorites")}</h2>${this.scenes("favorite", !0)}
          ${this.has("experience") ? b`<h2>${P("nav.experiences")}</h2>${this.scenes("experience", !0)}` : C}
        </div>
      </div>` : this.tab === "favorites" ? this.scenes("favorite") : this.tab === "experiences" ? this.scenes("experience") : b`<div class="layout"><div class="col">${e}</div></div>`;
	}
}), j("kkp-toggle", class extends E {
	static {
		this.properties = {
			checked: { type: Boolean },
			disabled: { type: Boolean },
			label: {}
		};
	}
	static {
		this.styles = [A, o`
      :host {
        display: inline-block;
      }

      button {
        position: relative;
        width: 64px;
        height: 36px;
        border-radius: var(--kk-radius-pill);
        background: var(--kk-control);
        transition: background 200ms var(--kk-ease);
      }

      button[aria-checked="false"] {
        box-shadow: inset 0 0 0 1.5px var(--kk-text-2);
      }

      button[aria-checked="true"] {
        background: var(--kk-accent-fill);
      }

      span {
        position: absolute;
        top: 4px;
        left: 4px;
        width: 40px;
        height: 28px;
        border-radius: var(--kk-radius-pill);
        background: #fff;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
        transition: transform 220ms var(--kk-ease);
      }

      button[aria-checked="true"] span {
        transform: translateX(16px);
      }

      button:disabled {
        opacity: 0.4;
      }
    `];
	}
	render() {
		return b`<button
      role="switch"
      aria-checked=${this.checked ? "true" : "false"}
      aria-label=${this.label ?? ""}
      ?disabled=${this.disabled}
      @click=${() => this.dispatchEvent(new CustomEvent("change", { detail: !this.checked }))}
    >
      <span></span>
    </button>`;
	}
}), j("kkp-diagnostics", class extends q {
	static {
		this.properties = {
			...q.properties,
			details: { state: !0 }
		};
	}
	constructor() {
		super(), this.details = null;
	}
	static {
		this.styles = [
			A,
			R,
			o`
      :host {
        display: block;
        max-width: 760px;
        padding-bottom: 48px;
      }
    `
		];
	}
	async firstUpdated() {
		try {
			this.details = await this.ctx.api.deviceDetails(this.device.id);
		} catch (e) {
			this.ctx.toast(K(e), "error");
		}
	}
	connection(e) {
		return e == null ? b`<span class="dot"></span>${P("diagnostics.unknown")}` : e ? b`<span class="dot good"></span>${P("diagnostics.connected")}` : b`<span class="dot bad"></span>${P("diagnostics.disconnected")}`;
	}
	async download() {
		let e = this.details?.diagnostics_path;
		if (e) try {
			window.open(await this.ctx.api.signPath(e), "_blank");
		} catch (e) {
			this.ctx.toast(K(e), "error");
		}
	}
	render() {
		let e = this.details?.faults ?? [], t = this.entity("report_log");
		return b`
      ${I(null, b`
          ${F({
			label: P("diagnostics.stream"),
			description: P("diagnostics.stream_body"),
			value: this.connection(this.live?.stream_connected)
		})}
          ${F({
			label: P("diagnostics.cloud"),
			description: P("diagnostics.cloud_body"),
			value: this.connection(this.live?.cloud_connected)
		})}
          ${F({
			label: P("diagnostics.last_update"),
			value: this.live?.last_update ? bt(this.live.last_update) : "—"
		})}
        `)}
      ${I(P("diagnostics.faults"), b`
          ${e.length ? e.map((e) => F({
			label: e.title ?? e.code ?? "—",
			description: [
				e.code,
				e.area,
				e.time ? yt(e.time) : null
			].filter(Boolean).join(" · ")
		})) : F({ label: P("diagnostics.no_faults") })}
          ${this.has("clear_errors") ? F({
			label: P("diagnostics.clear_errors"),
			trailing: O("restart"),
			onClick: () => void this.roleService("clear_errors", "button", "press")
		}) : C}
        `)}
      ${I(P("settings.diagnostics"), b`
          ${t ? F({
			label: P("diagnostics.report_log"),
			description: t.attributes.file ?? P("diagnostics.report_log_body"),
			trailing: b`<kkp-toggle
                  .checked=${t.state === "on"}
                  label=${P("diagnostics.report_log")}
                  @change=${(e) => void this.roleService("report_log", "switch", e.detail ? "turn_on" : "turn_off")}
                ></kkp-toggle>`
		}) : C}
          ${this.details?.diagnostics_path ? F({
			label: P("diagnostics.download"),
			description: P("diagnostics.download_body"),
			trailing: O("download"),
			onClick: () => this.download()
		}) : C}
        `)}
    `;
	}
});
// src/art/products.ts
function Mt(e, t = 2.2) {
	return x`
  <g transform=${e} stroke-width=${t}>
    <line x1="0" y1="-22" x2="0" y2="-78" />
    <rect x="-8" y="-22" width="16" height="13" rx="4" />
    <ellipse cx="0" cy="0" rx="36" ry="10.5" />
    <path d="M-36 0 V7 A36 10.5 0 0 0 36 7 V0" />
    <ellipse cx="0" cy="7" rx="29" ry="7" opacity="0.45" />
    <g class="spray">
      <line x1="-25" y1="22" x2="-25" y2="60" />
      <line x1="-15" y1="23" x2="-15" y2="68" />
      <line x1="-5" y1="24" x2="-5" y2="72" />
      <line x1="5" y1="24" x2="5" y2="72" />
      <line x1="15" y1="23" x2="15" y2="68" />
      <line x1="25" y1="22" x2="25" y2="60" />
    </g>
  </g>`;
}
var Nt = Mt("translate(86 81) rotate(30)"), Pt = x`
  <path d="M108 140 V66 A36 36 0 0 0 36 66 V70" />
  <path d="M97 140 V66 A25 25 0 0 0 47 66 V70" />
  <path d="M33.5 70 H49.5 L51 92 A4 4 0 0 1 47 96.5 H37 A4 4 0 0 1 32 92 Z" />
  <line x1="35" y1="79" x2="48" y2="79" opacity="0.5" />
  <path class="accent" d="M53 50 A14 14 0 0 1 63 42.5" />
  <path d="M84 140 H122" />
  <circle cx="113" cy="118" r="6" />
  <path d="M117 113 L127 97" />
  <line class="stream" x1="41.5" y1="101" x2="41.5" y2="152" />`, Ft = x`
  <line x1="12" y1="44" x2="148" y2="44" opacity="0.45" />
  <rect x="48" y="26" width="64" height="18" rx="2" opacity="0.45" />
  <rect x="22" y="44" width="116" height="12" rx="2" />
  <path d="M28 56 L42 70 H118 L132 56" />
  <g opacity="0.45">
    <line x1="52" y1="60" x2="52" y2="66" />
    <line x1="62" y1="60" x2="62" y2="66" />
    <line x1="72" y1="60" x2="72" y2="66" />
    <line x1="88" y1="60" x2="88" y2="66" />
    <line x1="98" y1="60" x2="98" y2="66" />
    <line x1="108" y1="60" x2="108" y2="66" />
  </g>
  <path class="accent" d="M60 74 H100" />
  <g class="spray">
    <line x1="44" y1="118" x2="44" y2="86" />
    <line x1="62" y1="130" x2="62" y2="86" />
    <line x1="80" y1="134" x2="80" y2="86" />
    <line x1="98" y1="130" x2="98" y2="86" />
    <line x1="116" y1="118" x2="116" y2="86" />
  </g>`, It = x`
  <path d="M104 117 V37 A6 6 0 0 1 110 31 H126 A6 6 0 0 1 132 37 V117" />
  <path d="M104 33 H31 A3 3 0 0 0 28 36 V38 A3 3 0 0 0 31 41 H104" />
  <path d="M32 41 L50 117 H132" />
  <line x1="34" y1="46" x2="104" y2="46" opacity="0.45" />
  <path d="M58 117 V123 H124 V117" />
  <path class="accent" d="M54 129 H128" />`, Lt = x`
  <g transform="translate(79 82) rotate(-22) scale(0.84)" stroke-width="2.62">
    <path d="M34 -24 H-22 C-58 -24 -78 -12 -78 0 C-78 12 -58 24 -22 24 H34" />
    <path d="M-78 0 V7 C-78 19 -58 31 -22 31 H34" />
    <path d="M-62 0 C-62 -8 -46 -14 -24 -14 H22 C28 -14 28 14 22 14 H-24 C-46 14 -62 8 -62 0 Z"
      opacity="0.45" />
    <path d="M34 -32 H54 A6 6 0 0 1 60 -26 V32 A6 6 0 0 1 54 38 H34 Z" />
    <line x1="40" y1="-24" x2="40" y2="30" opacity="0.45" />
    <path d="M38 38 V46 A4 4 0 0 0 42 50 H58 A4 4 0 0 0 62 46 V36" />
    <g class="accent">
      <circle cx="45" cy="44" r="1.6" />
      <circle cx="51" cy="44" r="1.6" />
      <circle cx="57" cy="44" r="1.6" />
    </g>
  </g>`, Rt = x`
  ${Mt("translate(69 72) rotate(30) scale(0.84)", 2.62)}
  <g class="accent">
    <path d="M105 120 A16 16 0 0 1 133 114 M133 114 L134 106 M133 114 L126 112" />
    <path d="M135 130 A16 16 0 0 1 107 136 M107 136 L106 144 M107 136 L114 138" />
  </g>`, zt = x`
  <path d="M20 60 H112 L140 40 H48 Z" />
  <path d="M20 60 V114 A6 6 0 0 0 26 120 H106 A6 6 0 0 0 112 114 V60" />
  <path d="M112 120 L134 104 A6 6 0 0 0 140 98 V40" />
  <path d="M42 56 H98 L114 45 H58 Z" opacity="0.45" />
  <path class="accent" d="M48 52 C54 49 60 55 66 52 S78 49 84 52 S96 55 102 51" />
  <rect x="104" y="46" width="22" height="9" rx="4.5" transform="rotate(-35 115 50.5)" />`, Bt = x`
  <circle cx="80" cy="80" r="56" opacity="0.4" />
  <path d="M80 40 C80 40 54 72 54 92 A26 26 0 0 0 106 92 C106 72 80 40 80 40 Z" />
  <path class="accent" d="M68 94 A12 12 0 0 0 80 106" />`, Vt = {
	valve: Nt,
	controller: Nt,
	faucet: Pt,
	smart_fan: Ft,
	toilet: It,
	evocycle: Rt,
	ice_bath: zt
};
function Z(e, t) {
	return x`<svg class="art" viewBox="0 0 160 160" fill="none" stroke="currentColor"
    stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${e === "toilet" && t?.startsWith("Purewash") ? Lt : Vt[e] ?? Bt}</svg>`;
}
// src/theme/art.ts
var Q = o`
  .art {
    display: block;
    width: 100%;
    height: 100%;
    overflow: visible;
    color: var(--kk-art);
  }

  .art .accent {
    stroke: var(--kk-accent);
  }

  .art .spray line {
    stroke-dasharray: 3 8;
    opacity: 0.85;
  }

  .art .stream {
    opacity: 0;
    stroke: var(--kk-accent);
    stroke-dasharray: 5 6;
    transition: opacity 400ms var(--kk-ease);
  }

  .running .art .spray line,
  .running .fixture .spray line {
    stroke: var(--kk-accent);
    animation: kk-fall 0.9s linear infinite;
  }

  .running .art .stream {
    opacity: 1;
    animation: kk-fall 0.6s linear infinite;
  }

  @keyframes kk-fall {
    to {
      stroke-dashoffset: -22;
    }
  }
`;
j("kkp-faucet-ring", class extends E {
	static {
		this.properties = { flow: {} };
	}
	constructor() {
		super(), this.flow = "off";
	}
	static {
		this.styles = [
			A,
			Q,
			o`
      :host {
        display: grid;
        place-items: center;
      }

      .ring {
        position: relative;
        display: grid;
        place-items: center;
        width: min(70vw, 260px);
        aspect-ratio: 1;
        border-radius: 50%;
        box-shadow: inset 0 0 0 3px var(--kk-control), 0 0 40px rgba(0, 0, 0, 0.35);
        transition: box-shadow 400ms var(--kk-ease);
      }

      .ring.running {
        box-shadow: inset 0 0 0 3px var(--kk-accent-line), 0 0 60px var(--kk-accent-soft);
      }

      .art-wrap {
        width: 58%;
        aspect-ratio: 1;
      }

      .sweep {
        position: absolute;
        inset: -3px;
        border-radius: 50%;
        background: conic-gradient(from 0deg, transparent 0 70%, var(--kk-accent) 100%);
        -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 4px), #000 0);
        mask: radial-gradient(farthest-side, transparent calc(100% - 4px), #000 0);
        animation: kk-spin 1.6s linear infinite;
      }

      .halo {
        position: absolute;
        inset: 14%;
        border-radius: 50%;
        background: radial-gradient(circle, var(--kk-accent-soft), transparent 70%);
        opacity: 0;
        transition: opacity 600ms var(--kk-ease);
      }

      .running .halo {
        opacity: 1;
        animation: kk-breathe 4.8s ease-in-out infinite;
      }

      @keyframes kk-spin {
        to {
          transform: rotate(1turn);
        }
      }

      @keyframes kk-breathe {
        50% {
          transform: scale(1.08);
        }
      }
    `
		];
	}
	render() {
		return b`<div class="ring ${this.flow === "off" ? "" : "running"}">
      <div class="halo"></div>
      ${this.flow === "dispensing" ? b`<div class="sweep"></div>` : null}
      <div class="art-wrap">${Z("faucet")}</div>
    </div>`;
	}
}), j("kkp-faucet", class extends q {
	static {
		this.properties = {
			...q.properties,
			wide: { type: Boolean },
			mode: { state: !0 },
			amount: { state: !0 },
			sheetOpen: { state: !0 },
			now: { state: !0 }
		};
	}
	constructor() {
		super(), this.mode = "faucet", this.amount = null, this.sheetOpen = !1, this.now = Date.now();
	}
	connectedCallback() {
		super.connectedCallback(), this.ticker = window.setInterval(() => this.now = Date.now(), 1e3);
	}
	disconnectedCallback() {
		super.disconnectedCallback(), window.clearInterval(this.ticker);
	}
	static {
		this.styles = [
			A,
			z,
			o`
      :host {
        display: block;
      }

      .layout {
        display: grid;
        gap: 24px;
      }

      :host([wide]) .layout {
        grid-template-columns: minmax(0, 1.1fr) minmax(320px, 1fr);
        align-items: start;
        gap: 32px;
      }

      .stage {
        display: grid;
        justify-items: center;
        gap: 26px;
        padding: 18px 0 8px;
      }

      .status {
        min-height: 22px;
        margin-top: -6px;
        font-size: 16px;
        color: var(--kk-text-2);
        text-align: center;
      }

      .status.active {
        color: var(--kk-accent-text);
      }

      .start {
        min-width: 150px;
        min-height: 50px;
        font-size: 20px;
      }

      .modes {
        display: flex;
        justify-content: center;
        gap: 56px;
      }

      .mode {
        display: grid;
        justify-items: center;
        gap: 12px;
        width: 120px;
        font-size: 18px;
        color: var(--kk-text-2);
      }

      .mode .circle {
        display: grid;
        place-items: center;
        width: 76px;
        height: 76px;
        border-radius: 50%;
        background: var(--kk-control);
        color: var(--kk-text);
        transition: background 220ms var(--kk-ease), transform 160ms var(--kk-ease);
      }

      .mode .circle svg {
        width: 34px;
        height: 34px;
      }

      .mode[aria-pressed="true"] {
        color: var(--kk-text);
      }

      .mode[aria-pressed="true"] .circle {
        background: var(--kk-accent-fill);
        color: var(--kk-accent-on);
      }

      .mode:active .circle {
        transform: scale(0.95);
      }

      .amounts {
        display: grid;
        gap: 16px;
        width: 100%;
        padding: 20px;
      }

      .chips {
        display: flex;
        gap: 8px;
        overflow-x: auto;
        padding-bottom: 4px;
        scrollbar-width: none;
      }

      .chip {
        flex: none;
        padding: 10px 16px;
        border-radius: var(--kk-radius-pill);
        background: var(--kk-control);
        font-size: 15px;
        transition: background 160ms var(--kk-ease);
      }

      .chip[aria-pressed="true"] {
        background: var(--kk-accent-fill);
        color: var(--kk-accent-on);
      }

      .stepper {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 14px;
      }

      .stepper strong {
        font-size: 30px;
        font-weight: 300;
        font-variant-numeric: tabular-nums;
      }

      .stepper .label {
        font-size: 15px;
        color: var(--kk-text-2);
      }

      .sheet {
        border-radius: 22px 22px var(--kk-radius-card) var(--kk-radius-card);
        background: linear-gradient(180deg, var(--kk-sheet), var(--kk-surface) 140px);
        box-shadow: var(--kk-shadow);
        overflow: hidden;
      }

      .sheet-toggle {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        width: 100%;
        padding: 18px;
        font-size: 19px;
        color: var(--kk-text-2);
        border-top: 1px solid var(--kk-accent-line);
      }

      /* Inside, since the sheet clips anything drawn beyond its edges. */
      .sheet-toggle:focus-visible {
        outline-offset: -3px;
      }

      .sheet-toggle svg {
        width: 22px;
        height: 22px;
        transition: transform 240ms var(--kk-ease);
        transform: rotate(180deg);
      }

      .sheet-toggle[aria-expanded="true"] svg {
        transform: none;
      }

      .presets-head {
        margin: 0;
        padding: 6px 20px 10px;
        font-size: 18px;
        font-weight: 400;
      }

      .preset {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 16px 20px;
        border-top: 1px solid var(--kk-divider);
      }

      .preset-name {
        font-size: 18px;
      }

      .preset-amount {
        display: block;
        font-size: 14px;
        color: var(--kk-text-2);
        margin-top: 2px;
      }

      .preset .pill {
        min-height: 40px;
        padding: 0 22px;
      }

      .empty {
        padding: 18px 20px 24px;
        color: var(--kk-text-2);
      }

      .banners {
        display: grid;
        gap: 10px;
      }

      :host([wide]) .sheet-toggle {
        display: none;
      }
    `
		];
	}
	get presets() {
		let e = this.device.capabilities.presets;
		return Array.isArray(e) ? e : [];
	}
	get quickAmounts() {
		return this.device.quick_amounts ?? [];
	}
	get unit() {
		return this.device.units.dispense ?? this.quickAmounts[0]?.unit ?? "mL";
	}
	get metric() {
		return this.unit.toLowerCase().includes("l");
	}
	start() {
		if (this.mode === "faucet") {
			let e = this.entityId("water");
			e && this.queued("water", "switch", "turn_on", { entity_id: e });
			return;
		}
		let e = this.amount ?? this.quickAmounts[0]?.amount;
		e && this.queued("water", "kohler_konnect", "dispense", {
			device_id: this.device.id,
			amount: e,
			unit: this.metric ? "ml" : "fl_oz"
		});
	}
	stop() {
		let e = this.entityId("water");
		e && this.stopNow("switch", "turn_off", { entity_id: e });
	}
	pour(e) {
		this.queued("water", "kohler_konnect", "dispense", {
			device_id: this.device.id,
			preset: e.name
		});
	}
	step(e) {
		let t = this.metric ? 50 : 1, n = this.metric ? 50 : 1, r = this.metric ? 11360 : 384, i = this.amount ?? this.quickAmounts[0]?.amount ?? n;
		this.amount = Math.min(r, Math.max(n, Math.round((i + e * t) / t) * t));
	}
	formatAmount(e) {
		let t = this.quickAmounts.find((t) => Math.abs(t.amount - e) < 1e-6);
		return t ? t.label : `${J(e, +!this.metric)} ${this.unit}`;
	}
	renderStatus(e, t) {
		if (t) return b`<div class="status active">${this.entity("dispensing")?.attributes.preset ?? P("status.dispensing")}</div>`;
		if (e) {
			let e = this.live?.auto_off_at ? Date.parse(this.live.auto_off_at) : NaN, t = Number.isFinite(e) ? Math.max(0, (e - this.now) / 1e3) : null;
			return b`<div class="status active">
        ${t === null ? P("status.water_on") : P("faucet.auto_off", { time: gt(t) })}
      </div>`;
		}
		let n = this.entity("last_dispensed");
		return b`<div class="status">
      ${n && n.state !== "unknown" && n.state !== "unavailable" ? P("faucet.last_dispensed", { amount: `${n.state} ${n.attributes.unit_of_measurement ?? ""}`.trim() }) : C}
    </div>`;
	}
	renderBanners() {
		let e = [];
		return this.isOn("leak") && e.push(L("danger", P("faucet.leak"), {
			label: P("faucet.clear_leak"),
			onClick: () => void this.roleService("clear_leak", "button", "press")
		})), this.entity("handle")?.state === "closed" && e.push(L("info", P("faucet.handle_closed"))), this.entity("firmware")?.attributes.in_progress && e.push(L("warning", P("faucet.firmware_downloading"))), e.length ? b`<div class="banners">${e}</div>` : C;
	}
	renderAmounts() {
		let e = this.amount ?? this.quickAmounts[0]?.amount ?? null;
		return b`<div class="card amounts">
      <div class="chips" role="group" aria-label=${P("faucet.dispense_amount")}>
        ${this.quickAmounts.map((t) => b`<button
            class="chip"
            aria-pressed=${String(e === t.amount)}
            @click=${() => this.amount = t.amount}
          >
            ${t.label}
          </button>`)}
      </div>
      <div class="stepper">
        <button class="round" aria-label=${P("control.decrease", { name: P("faucet.custom") })} @click=${() => this.step(-1)}>
          ${O("minus")}
        </button>
        <div style="text-align:center">
          <strong aria-live="polite">${e === null ? "—" : this.formatAmount(e)}</strong>
          <div class="label">${P("faucet.custom")}</div>
        </div>
        <button class="round" aria-label=${P("control.increase", { name: P("faucet.custom") })} @click=${() => this.step(1)}>
          ${O("plus")}
        </button>
      </div>
    </div>`;
	}
	renderPresets() {
		return b`
      <h2 class="presets-head">${P("faucet.presets")}</h2>
      ${this.presets.length ? this.presets.map((e, t) => b`<div class="preset">
              <div id="preset-${t}">
                <span class="preset-name">${e.name}</span>
                <span class="preset-amount">${J(e.amount, 2)} ${e.unit}</span>
              </div>
              <button class="pill" aria-describedby="preset-${t}" @click=${() => this.pour(e)}>
                ${P("faucet.start")}
              </button>
            </div>`) : b`<div class="empty">${P("faucet.no_presets")}</div>`}
    `;
	}
	render() {
		let e = this.isOn("dispensing"), t = e || this.isOn("water") || this.isOn("status"), n = !this.has("water"), r = b`<div class="stage">
      ${this.renderBanners()}
      <kkp-faucet-ring .flow=${e ? "dispensing" : t ? "on" : "off"}></kkp-faucet-ring>
      ${this.renderStatus(t, e)}
      <button class="pill start" ?disabled=${!t && n} @click=${t ? this.stop : this.start}>
        ${t ? b`${O("stop")}${P("faucet.stop")}` : P("faucet.start")}
      </button>
      <div class="modes">
        <button class="mode" aria-pressed=${String(this.mode === "faucet")} @click=${() => this.mode = "faucet"}>
          <span class="circle">${O("faucet")}</span>${P("faucet.faucet")}
        </button>
        <button class="mode" aria-pressed=${String(this.mode === "dispense")} @click=${() => this.mode = "dispense"}>
          <span class="circle">${O("cup")}</span>${P("faucet.dispense_amount")}
        </button>
      </div>
      ${this.mode === "dispense" ? this.renderAmounts() : C}
    </div>`, i = this.statusRegion(P(e ? "status.dispensing" : t ? "status.water_on" : "status.off"));
		return this.wide ? b`${i}<div class="layout">${r}<div class="sheet">${this.renderPresets()}</div></div>` : b`${i}<div class="layout">
      ${r}
      <div class="sheet">
        <button class="sheet-toggle" aria-expanded=${String(this.sheetOpen)} @click=${() => this.sheetOpen = !this.sheetOpen}>
          ${O("chevronDown")}${this.sheetOpen ? P("faucet.see_less") : P("faucet.see_more")}
        </button>
        ${this.sheetOpen ? this.renderPresets() : C}
      </div>
    </div>`;
	}
});
// src/components/slider.ts
var Ht = 400, Ut = 5e3;
j("kkp-slider", class extends E {
	static {
		this.properties = {
			label: {},
			value: { type: Number },
			min: { type: Number },
			max: { type: Number },
			step: { type: Number },
			unit: {},
			variant: {},
			coldValue: {
				type: Number,
				attribute: !1
			},
			disabled: { type: Boolean },
			compact: { type: Boolean },
			draft: { state: !0 }
		};
	}
	constructor() {
		super(), this.dragging = !1, this.value = null, this.min = 0, this.max = 100, this.step = 1, this.unit = "", this.variant = "temperature", this.coldValue = null, this.disabled = !1, this.compact = !1, this.draft = null;
	}
	static {
		this.styles = [A, o`
      :host {
        display: block;
      }

      .head {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 18px;
      }

      .label {
        font-size: 20px;
        color: var(--kk-text);
      }

      :host([compact]) .label {
        font-size: 17px;
        color: var(--kk-text-2);
      }

      .value {
        font-size: 22px;
        color: var(--kk-accent-text);
        font-variant-numeric: tabular-nums;
      }

      .row {
        display: flex;
        align-items: center;
        gap: 14px;
      }

      .track {
        position: relative;
        flex: 1;
        height: 48px;
        touch-action: none;
        cursor: pointer;
      }

      .track[aria-disabled="true"] {
        cursor: default;
        opacity: 0.4;
      }

      .rail,
      .fill {
        position: absolute;
        top: 50%;
        left: 0;
        right: 0;
        height: 10px;
        margin-top: -5px;
        border-radius: var(--kk-radius-pill);
      }

      .temperature .rail {
        background: var(--kk-temp-gradient);
      }

      .flow .rail {
        background: var(--kk-control);
      }

      .flow .fill {
        right: auto;
        background: linear-gradient(90deg, var(--kk-bar-bottom), var(--kk-bar-top));
      }

      .thumb {
        position: absolute;
        top: 50%;
        width: 40px;
        height: 40px;
        margin: -20px 0 0 -20px;
        border-radius: 50%;
        background: #fff;
        border: 2px solid var(--kk-thumb-edge);
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.35);
        transition: transform 160ms var(--kk-ease);
      }

      .track.dragging .thumb {
        transform: scale(1.08);
      }

      .track:focus-visible {
        outline: none;
      }

      .track:focus-visible .thumb {
        outline: 2px solid var(--kk-accent);
        outline-offset: 3px;
      }

      /* With no value there is no thumb: ring the track instead. */
      .track.empty:focus-visible {
        outline: 2px solid var(--kk-accent);
        outline-offset: 3px;
        border-radius: var(--kk-radius-pill);
      }


    `];
	}
	disconnectedCallback() {
		super.disconnectedCallback(), window.clearTimeout(this.commitTimer), window.clearTimeout(this.draftTimer);
	}
	willUpdate(e) {
		e.has("value") && !this.dragging && this.commitTimer === void 0 && (this.draft = null);
	}
	get shown() {
		return this.draft ?? this.value;
	}
	format(e) {
		return e === null ? "—" : this.coldValue !== null && e <= this.coldValue ? P("shower.cold") : this.variant === "temperature" ? Y(e, this.unit) : !this.unit || this.unit === "%" ? `${Math.round(e)}%` : `${J(e, +!Number.isInteger(this.step))} ${this.unit}`;
	}
	fraction(e) {
		return e === null || this.max <= this.min ? 0 : Math.min(1, Math.max(0, (e - this.min) / (this.max - this.min)));
	}
	snap(e) {
		let t = Math.round((e - this.min) / this.step) * this.step + this.min;
		return Math.min(this.max, Math.max(this.min, Number(t.toFixed(4))));
	}
	setDraft(e, t) {
		this.draft = this.snap(e), window.clearTimeout(this.commitTimer), this.commitTimer = void 0, t === "now" && this.commit(), t === "soon" && (this.commitTimer = window.setTimeout(() => this.commit(), Ht));
	}
	commit() {
		window.clearTimeout(this.commitTimer), this.commitTimer = void 0, this.draft === null || this.draft === this.value ? this.draft = null : (this.dispatchEvent(new CustomEvent("change", { detail: this.draft })), window.clearTimeout(this.draftTimer), this.draftTimer = window.setTimeout(() => {
			this.draft = null;
		}, Ut));
	}
	valueAt(e) {
		let t = e.currentTarget.getBoundingClientRect(), n = t.width - 40, r = Math.min(1, Math.max(0, (e.clientX - t.left - 20) / n));
		return this.min + r * (this.max - this.min);
	}
	onPointerDown(e) {
		this.disabled || (e.currentTarget.setPointerCapture(e.pointerId), this.dragging = !0, this.setDraft(this.valueAt(e), "none"), this.requestUpdate());
	}
	onPointerMove(e) {
		this.dragging && this.setDraft(this.valueAt(e), "none");
	}
	onPointerUp() {
		this.dragging && (this.dragging = !1, this.commit(), this.requestUpdate());
	}
	nudge(e, t = 1) {
		if (this.disabled) return;
		let n = this.shown ?? this.min;
		this.setDraft(n + e * this.step * t, "soon");
	}
	onKey(e) {
		let t = {
			ArrowRight: () => this.nudge(1),
			ArrowUp: () => this.nudge(1),
			ArrowLeft: () => this.nudge(-1),
			ArrowDown: () => this.nudge(-1),
			PageUp: () => this.nudge(1, 5),
			PageDown: () => this.nudge(-1, 5),
			Home: () => this.setDraft(this.min, "soon"),
			End: () => this.setDraft(this.max, "soon")
		}[e.key];
		t && !this.disabled && (e.preventDefault(), t());
	}
	render() {
		let e = this.shown, t = `calc(20px + (100% - 40px) * ${this.fraction(e)})`;
		return b`
      <div class="head">
        <span class="label">${this.label}</span>
        <span class="value">${this.format(e)}</span>
      </div>
      <div class="row">
        <button
          class="round"
          aria-label=${P("control.decrease", { name: this.label })}
          ?disabled=${this.disabled}
          aria-disabled=${String((e ?? this.min) <= this.min)}
          @click=${() => this.nudge(-1)}
        >
          ${O("minus")}
        </button>
        <div
          class="track ${this.variant} ${this.dragging ? "dragging" : ""} ${e === null ? "empty" : ""}"
          role="slider"
          tabindex=${this.disabled ? -1 : 0}
          aria-label=${this.label}
          aria-valuemin=${this.min}
          aria-valuemax=${this.max}
          aria-valuenow=${e ?? C}
          aria-valuetext=${this.format(e)}
          aria-disabled=${this.disabled ? "true" : "false"}
          @pointerdown=${this.onPointerDown}
          @pointermove=${this.onPointerMove}
          @pointerup=${this.onPointerUp}
          @pointercancel=${this.onPointerUp}
          @keydown=${this.onKey}
        >
          <div class="rail"></div>
          ${this.variant === "flow" ? b`<div class="fill" style="width: ${t}"></div>` : C}
          ${e === null ? C : b`<div class="thumb" style="left: ${t}"></div>`}
        </div>
        <button
          class="round"
          aria-label=${P("control.increase", { name: this.label })}
          ?disabled=${this.disabled}
          aria-disabled=${String((e ?? this.max) >= this.max)}
          @click=${() => this.nudge(1)}
        >
          ${O("plus")}
        </button>
      </div>
    `;
	}
});
// src/components/editors.ts
function Wt(e) {
	let t = e.choices.includes(e.current);
	return b`<div class="editor">
    <div class="choices" role="radiogroup" aria-label=${e.label} @keydown=${lt}>
      ${e.choices.map((n, r) => b`<button
          class="choice"
          role="radio"
          aria-checked=${String(n === e.current)}
          tabindex=${ut(n === e.current, r, t)}
          @click=${() => e.onPick(n)}
        >
          <span>${e.display(n)}</span><span class="radio"></span>
        </button>`)}
    </div>
  </div>`;
}
function Gt(e) {
	let t = e.draft;
	return b`<div class="editor">
    <kkp-slider
      compact
      label=${e.label}
      .variant=${e.variant}
      .value=${t ?? e.value}
      .min=${e.min}
      .max=${e.max}
      .step=${e.step}
      unit=${e.unit}
      @change=${(t) => e.onDraft(t.detail)}
    ></kkp-slider>
    <div class="editor-actions">
      <button class="pill" @click=${e.onCancel}>${P("settings.cancel")}</button>
      <button
        class="pill filled"
        ?disabled=${t === null || t === e.value}
        @click=${() => t !== null && e.onSave(t)}
      >
        ${P("settings.save")}
      </button>
    </div>
  </div>`;
}
// src/views/generic.ts
var Kt = /* @__PURE__ */ new Set([
	"switch",
	"button",
	"select",
	"number",
	"light",
	"fan"
]), qt = /* @__PURE__ */ new Set(["report_log"]), Jt = /* @__PURE__ */ new Set(["calendar"]), Yt = /* @__PURE__ */ new Set(["start_rapid_clean", "start_deep_clean"]);
j("kkp-generic", class extends q {
	static {
		this.properties = {
			...q.properties,
			open: { state: !0 },
			draft: { state: !0 },
			pressing: { state: !0 }
		};
	}
	constructor() {
		super(), this.open = null, this.draft = null, this.pressing = /* @__PURE__ */ new Set();
	}
	static {
		this.styles = [
			A,
			R,
			z,
			Q,
			o`
      :host {
        display: block;
        max-width: 760px;
        padding-bottom: 48px;
      }

      .art {
        width: 140px;
        height: 140px;
        margin: 8px auto 4px;
      }
    `
		];
	}
	name(e) {
		let t = String(e.attributes.friendly_name ?? e.entity_id);
		return t.startsWith(`${this.device.name} `) ? t.slice(this.device.name.length + 1) : t;
	}
	toggleOpen(e, t = null) {
		this.open = this.open === e ? null : e, this.draft = t;
	}
	async closeEditor(e) {
		this.open = null, await this.updateComplete, this.renderRoot.querySelector(`[data-key="${e}"]`)?.focus();
	}
	async pick(e, t) {
		this.closeEditor(e);
		let n = this.entityId(e);
		n && await this.queued(e, "select", "select_option", {
			entity_id: n,
			option: t
		});
	}
	async save(e, t) {
		this.closeEditor(e);
		let n = this.entityId(e);
		n && await this.queued(e, "number", "set_value", {
			entity_id: n,
			value: t
		});
	}
	async press(e) {
		if (!Yt.has(e) || await this.ctx.confirm({
			title: P("generic.ozone_title"),
			body: P("generic.ozone_body"),
			confirmLabel: P("generic.ozone_confirm")
		})) {
			this.pressing = /* @__PURE__ */ new Set([...this.pressing, e]);
			try {
				await this.roleService(e, "button", "press");
			} finally {
				this.pressing = new Set([...this.pressing].filter((t) => t !== e));
			}
		}
	}
	async light(e, t, n) {
		this.closeEditor(t);
		let r = this.entityId(e);
		r && await this.queued(e, "light", "turn_on", {
			entity_id: r,
			...n
		});
	}
	lightRows(e, t) {
		let n = this.name(t), r = t.attributes.supported_color_modes ?? [], i = t.attributes.effect_list ?? [], a = t.attributes.brightness, o = t.state === "on" && typeof a == "number" ? Math.round(a / 255 * 100) : null, s = `${e}:brightness`, c = `${e}:effect`, l = r.some((e) => e !== "onoff"), u = t.state === "on" ? t.attributes.effect : null;
		return b`
      ${l ? b`${F({
			label: P("generic.brightness", { name: n }),
			value: o === null ? "—" : `${o}%`,
			chevron: !0,
			expanded: this.open === s,
			key: s,
			onClick: () => this.toggleOpen(s, o)
		})}
          ${this.open === s ? Gt({
			label: P("generic.brightness", { name: n }),
			value: o,
			draft: this.draft,
			min: 10,
			max: 100,
			step: 10,
			unit: "%",
			variant: "flow",
			onDraft: (e) => this.draft = e,
			onCancel: () => void this.closeEditor(s),
			onSave: (t) => void this.light(e, s, { brightness_pct: t })
		}) : C}` : C}
      ${i.length ? b`${F({
			label: P("generic.effect", { name: n }),
			value: u ? dt(this.hass, t, "effect", u) : "—",
			chevron: !0,
			expanded: this.open === c,
			key: c,
			onClick: () => this.toggleOpen(c)
		})}
          ${this.open === c ? Wt({
			label: P("generic.effect", { name: n }),
			choices: i,
			current: String(u ?? ""),
			display: (e) => dt(this.hass, t, "effect", e),
			onPick: (t) => void this.light(e, c, { effect: t })
		}) : C}` : C}
    `;
	}
	control(e, t) {
		let n = t.entity_id.split(".")[0], r = G(this.device, e) ? P("status.untested") : void 0, i = t.state === "unavailable", a = this.open === e && !i;
		if (n === "select") {
			let n = Array.isArray(t.attributes.options) ? t.attributes.options : [];
			return b`${F({
				label: this.name(t),
				tag: r,
				value: W(this.hass, t),
				chevron: !0,
				expanded: a,
				key: e,
				disabled: i,
				onClick: () => this.toggleOpen(e)
			})}
        ${a ? Wt({
				label: this.name(t),
				choices: n,
				current: t.state,
				display: (e) => W(this.hass, t, e),
				onPick: (t) => void this.pick(e, t)
			}) : C}`;
		}
		if (n === "number") {
			let n = U(t), o = String(t.attributes.unit_of_measurement ?? "");
			return b`${F({
				label: this.name(t),
				tag: r,
				value: W(this.hass, t),
				chevron: !0,
				expanded: a,
				key: e,
				disabled: i,
				onClick: () => this.toggleOpen(e, n)
			})}
        ${a ? Gt({
				label: this.name(t),
				value: n,
				draft: this.draft,
				min: Number(t.attributes.min ?? 0),
				max: Number(t.attributes.max ?? 100),
				step: Number(t.attributes.step ?? 1),
				unit: o,
				variant: t.attributes.device_class === "temperature" || /°[CF]/.test(o) ? "temperature" : "flow",
				onDraft: (e) => this.draft = e,
				onCancel: () => void this.closeEditor(e),
				onSave: (t) => void this.save(e, t)
			}) : C}`;
		}
		if (n === "switch" || n === "light" || n === "fan") {
			let a = F({
				label: this.name(t),
				tag: r,
				trailing: b`<kkp-toggle
          .checked=${t.state === "on"}
          ?disabled=${i}
          label=${this.name(t)}
          @change=${(t) => void this.roleService(e, n, t.detail ? "turn_on" : "turn_off")}
        ></kkp-toggle>`
			});
			return n === "light" ? b`${a}${this.lightRows(e, t)}` : a;
		}
		return F(n === "button" ? {
			label: this.name(t),
			tag: r,
			trailing: b`<button
          class="pill"
          aria-label=${P("generic.press_named", { name: this.name(t) })}
          ?disabled=${i || this.pressing.has(e)}
          @click=${() => void this.press(e)}
        >
          ${P("generic.press")}
        </button>`
		} : {
			label: this.name(t),
			tag: r,
			value: W(this.hass, t)
		});
	}
	render() {
		let e = Object.entries(this.device.roles).filter(([e]) => !qt.has(e)).map(([e, t]) => [e, this.hass.states[t]]).filter((e) => !!e[1]).filter(([, e]) => !Jt.has(e.entity_id.split(".")[0])).filter(([, e]) => !this.hass.entities?.[e.entity_id]?.hidden), t = e.filter(([, e]) => Kt.has(e.entity_id.split(".")[0])), n = e.filter(([, e]) => !Kt.has(e.entity_id.split(".")[0])), r = this.device.untested.length > 0;
		return b`
      <div class="art">${Z(this.device.kind, this.device.model)}</div>
      ${r ? L("info", P("generic.untested_body")) : C}
      ${t.length ? I(P("generic.controls"), t.map(([e, t]) => this.control(e, t))) : C}
      ${n.length ? I(P("generic.readings"), n.map(([, e]) => F({
			label: this.name(e),
			value: W(this.hass, e)
		}))) : C}
    `;
	}
}), j("kkp-tabs", class extends E {
	static {
		this.properties = {
			items: { attribute: !1 },
			active: {}
		};
	}
	static {
		this.styles = [A, o`
      :host {
        display: block;
        border-bottom: 1px solid var(--kk-divider);
      }

      div {
        display: flex;
      }

      button {
        flex: 1;
        position: relative;
        padding: 16px 8px 18px;
        font-size: 19px;
        color: var(--kk-text-3);
        transition: color 200ms var(--kk-ease);
      }

      button[aria-selected="true"] {
        color: var(--kk-text);
        font-weight: 500;
      }

      button::after {
        content: "";
        position: absolute;
        left: 50%;
        bottom: -1px;
        width: 40px;
        height: 3px;
        border-radius: 2px;
        background: var(--kk-accent);
        transform: translateX(-50%) scaleX(0);
        transition: transform 240ms var(--kk-ease);
      }

      button[aria-selected="true"]::after {
        transform: translateX(-50%) scaleX(1);
      }
    `];
	}
	onKey(e) {
		let t = [...this.renderRoot.querySelectorAll("[role=\"tab\"]")], n = t.indexOf(e.target), r = t.length - 1, i = {
			ArrowRight: n === r ? 0 : n + 1,
			ArrowLeft: n === 0 ? r : n - 1,
			Home: 0,
			End: r
		};
		n < 0 || !(e.key in i) || (e.preventDefault(), t.forEach((t, n) => t.tabIndex = n === i[e.key] ? 0 : -1), t[i[e.key]].focus());
	}
	render() {
		return b`<div role="tablist" @keydown=${this.onKey}>
      ${this.items.map((e) => b`<button
          role="tab"
          aria-selected=${e.key === this.active ? "true" : "false"}
          tabindex=${e.key === this.active ? 0 : -1}
          @click=${() => this.dispatchEvent(new CustomEvent("kkp-select", { detail: e.key }))}
        >
          ${e.label}
        </button>`)}
    </div>`;
	}
}), j("kkp-account", class extends E {
	static {
		this.properties = {
			hass: { attribute: !1 },
			data: { attribute: !1 },
			ctx: { attribute: !1 },
			options: { state: !0 },
			drafts: { state: !0 },
			saving: { state: !0 }
		};
	}
	constructor() {
		super(), this.options = {}, this.drafts = {}, this.saving = null;
	}
	static {
		this.styles = [
			A,
			R,
			z,
			o`
      :host {
        display: block;
        max-width: 720px;
      }

      .stepper {
        display: flex;
        align-items: center;
        gap: 12px;
      }

      .stepper .round {
        width: 40px;
        height: 40px;
      }

      .stepper strong {
        min-width: 72px;
        text-align: center;
        font-weight: 400;
        font-variant-numeric: tabular-nums;
      }

      .save {
        display: grid;
        justify-items: end;
        gap: 12px;
        margin-top: 18px;
      }

      .save .banner {
        width: 100%;
      }
    `
		];
	}
	firstUpdated() {
		this.load();
	}
	async load() {
		for (let e of this.data.entries) try {
			let t = await this.ctx.api.getOptions(e.entry_id);
			this.options = {
				...this.options,
				[e.entry_id]: t
			};
		} catch (e) {
			this.ctx.toast(K(e), "error");
		}
	}
	value(e, t) {
		return this.drafts[e]?.[t] ?? this.options[e]?.values[t];
	}
	set(e, t, n) {
		this.drafts = {
			...this.drafts,
			[e]: {
				...this.drafts[e],
				[t]: n
			}
		};
	}
	async save(e) {
		let t = this.drafts[e.entry_id];
		if (t && !this.saving) {
			this.saving = e.entry_id;
			try {
				let n = await this.ctx.api.updateOptions(e.entry_id, t), r = this.options[e.entry_id];
				this.options = {
					...this.options,
					[e.entry_id]: {
						...r,
						values: n.values
					}
				};
				let i = { ...this.drafts };
				delete i[e.entry_id], this.drafts = i, this.ctx.toast(P("account.saved")), await this.updateComplete;
				let a = this.data.entries.indexOf(e);
				this.renderRoot.querySelectorAll(".group h2")[a]?.focus();
			} catch (e) {
				this.ctx.toast(K(e), "error");
			} finally {
				this.saving = null;
			}
		}
	}
	async download(e) {
		try {
			let t = await this.ctx.api.signPath(`/api/diagnostics/config_entry/${e.entry_id}`);
			window.open(t, "_blank");
		} catch (e) {
			this.ctx.toast(K(e), "error");
		}
	}
	renderField(e, t) {
		let n = e.entry_id;
		if (t.kind === "toggle") return F({
			label: P("account.sidebar_panel"),
			description: P("account.sidebar_panel_body"),
			trailing: b`<kkp-toggle
          .checked=${!!this.value(n, t.key)}
          label=${P("account.sidebar_panel")}
          @change=${(e) => this.set(n, t.key, e.detail)}
        ></kkp-toggle>`
		});
		if (t.kind === "minutes") {
			let e = Number(this.value(n, t.key) ?? 0), [r, i] = [t.min ?? 0, t.max ?? 120];
			return F({
				label: P("account.safety_limit"),
				description: P("account.safety_limit_body"),
				trailing: b`<span class="stepper">
          <button class="round" aria-label=${P("control.decrease", { name: P("account.safety_limit") })} aria-disabled=${String(e <= r)}
            @click=${() => e > r && this.set(n, t.key, e - 1)}>${O("minus")}</button>
          <strong aria-live="polite">${e ? P("account.minutes", { count: e }) : P("account.off")}</strong>
          <button class="round" aria-label=${P("control.increase", { name: P("account.safety_limit") })} aria-disabled=${String(e >= i)}
            @click=${() => e < i && this.set(n, t.key, e + 1)}>${O("plus")}</button>
        </span>`
			});
		}
		let r = this.value(n, t.key);
		return b`<div class="row" style="display:block">
      <span class="label">${P("account.zone_grouping")}</span>
      <div
        class="choices"
        role="radiogroup"
        aria-label=${P("account.zone_grouping")}
        style="margin-top: 8px"
        @keydown=${lt}
      >
        ${(t.options ?? []).map((e, i, a) => b`<button
            class="choice"
            role="radio"
            aria-checked=${r === e ? "true" : "false"}
            tabindex=${ut(r === e, i, a.includes(String(r)))}
            @click=${() => this.set(n, t.key, e)}
          >
            <span>${P(`account.zone_grouping.${e}`)}</span><span class="radio"></span>
          </button>`)}
      </div>
    </div>`;
	}
	shownFields(e, t) {
		let n = this.data.devices.some((t) => t.entry_id === e.entry_id && t.zones.length > 1);
		return t.fields.filter((e) => e.key !== "zone_grouping" || n);
	}
	renderEntry(e) {
		let t = this.options[e.entry_id], n = this.drafts[e.entry_id], r = !!n && !!t?.fields.some((e) => e.reloads && e.key in n), i = e.stream_connected === null ? b`<span class="dot"></span>${P("diagnostics.unknown")}` : e.stream_connected ? b`<span class="dot good"></span>${P("diagnostics.connected")}` : b`<span class="dot bad"></span>${P("diagnostics.disconnected")}`;
		return b`
      ${I(e.title ?? P("app.title"), b`
          ${F({
			label: P("account.connection"),
			value: i
		})}
          ${t ? this.shownFields(e, t).map((t) => this.renderField(e, t)) : C}
          ${F({
			label: P("diagnostics.download"),
			description: P("diagnostics.download_body"),
			trailing: O("download"),
			onClick: () => this.download(e)
		})}
        `)}
      ${n ? b`<div class="save">
            ${r ? L("warning", P("account.reload_warning")) : C}
            <button class="pill filled" aria-disabled=${String(this.saving === e.entry_id)} @click=${() => this.save(e)}>
              ${P("account.save")}
            </button>
          </div>` : C}
    `;
	}
	render() {
		return this.ctx.admin ? this.data.entries.map((e) => this.renderEntry(e)) : L("info", P("account.admin_only"));
	}
}), j("kkp-home-usage", class extends E {
	static {
		this.properties = {
			hass: { attribute: !1 },
			data: { attribute: !1 },
			ctx: { attribute: !1 }
		};
	}
	static {
		this.styles = [
			A,
			Q,
			o`
      :host {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr));
        gap: 16px;
        padding-top: 28px;
      }

      .card {
        display: grid;
        gap: 18px;
        padding: 22px;
        text-align: left;
        transition: transform 200ms var(--kk-ease);
      }

      .card:hover {
        transform: translateY(-2px);
      }

      .top {
        display: flex;
        align-items: center;
        gap: 14px;
      }

      .thumb {
        width: 48px;
        height: 48px;
        flex: none;
      }

      .name {
        flex: 1;
        font-size: 18px;
      }

      .chev svg {
        width: 22px;
        height: 22px;
        color: var(--kk-text-2);
      }

      .figures {
        display: flex;
        gap: 28px;
      }

      .figure span {
        display: block;
        font-size: 14px;
        color: var(--kk-text-2);
      }

      .figure strong {
        font-size: 28px;
        font-weight: 300;
        font-variant-numeric: tabular-nums;
      }

      .spark {
        display: flex;
        align-items: flex-end;
        gap: 6px;
        height: 56px;
      }

      .spark i {
        flex: 1;
        border-radius: 4px;
        background: linear-gradient(var(--kk-bar-top), var(--kk-bar-bottom));
        min-height: 3px;
      }

      .spark i.empty {
        background: var(--kk-bar-track);
      }

      .caption {
        font-size: 13px;
        color: var(--kk-text-3);
      }
    `
		];
	}
	renderCard(e) {
		let t = U(B(this.hass, e, "water_month")), n = U(B(this.hass, e, "water_today")), r = B(this.hass, e, "water_week")?.attributes.per_day, i = r ? Object.values(r).slice(-7) : [], a = Math.max(...i, 0), o = e.units.volume;
		return b`<button class="card" @click=${() => this.ctx.navigate(`/d/${e.id}/usage`)}>
      <div class="top">
        <span class="thumb">${Z(e.kind, e.model)}</span>
        <span class="name">${e.name}</span>
        <span class="chev">${O("chevronRight")}</span>
      </div>
      <div class="figures">
        <div class="figure"><span>${P("home.usage_this_month")}</span><strong>${mt(t, o)}</strong></div>
        <div class="figure"><span>${P("home.usage_today")}</span><strong>${mt(n, o)}</strong></div>
      </div>
      ${i.length ? b`<div>
            <div class="spark" aria-hidden="true">
              ${i.map((e) => b`<i class=${e ? "" : "empty"} style="height:${a ? Math.max(6, e / a * 100) : 6}%"></i>`)}
            </div>
            <div class="caption" aria-hidden="true">${P("home.usage_last_7_days")}</div>
          </div>` : C}
    </button>`;
	}
	render() {
		return this.data.devices.filter((e) => e.roles.water_month || e.roles.water_today).map((e) => this.renderCard(e));
	}
});
// src/views/home.ts
var Xt = "https://github.com/kedube/ha-kohler-konnect/blob/main/docs/user_guide.md";
j("kkp-home", class extends E {
	static {
		this.properties = {
			hass: { attribute: !1 },
			data: { attribute: !1 },
			ctx: { attribute: !1 },
			tab: {},
			narrow: { type: Boolean }
		};
	}
	static {
		this.styles = [
			A,
			Q,
			o`
      :host {
        display: block;
      }

      .intro {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        padding: 28px 4px 26px;
      }

      .menu {
        margin: -4px 12px 0 -4px;
      }

      h1 {
        margin: 0;
        font-size: clamp(28px, 4vw, 36px);
        font-weight: 300;
        letter-spacing: -0.01em;
      }

      .summary {
        margin-top: 8px;
        font-size: 16px;
        color: var(--kk-text-2);
      }

      .summary .live {
        color: var(--kk-accent-text);
      }

      .summary .warn {
        color: var(--kk-danger);
      }

      .help {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 15px;
        color: var(--kk-text);
        text-decoration: none;
        white-space: nowrap;
      }

      .help svg {
        width: 26px;
        height: 26px;
      }

      :host([narrow]) .help span {
        display: none;
      }

      .area {
        margin: 34px 4px 14px;
      }

      /* Hairlines between tiles, as on the app's home grid: each tile draws its right and
         bottom edge, and the grid clips the last column's right edge. */
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 210px), 1fr));
        border-top: 1px solid var(--kk-divider);
        overflow: hidden;
      }

      :host([narrow]) .grid {
        grid-template-columns: repeat(2, 1fr);
        margin: 0 -16px;
      }

      .tile {
        position: relative;
        display: grid;
        justify-items: center;
        align-content: start;
        gap: 14px;
        margin-right: -1px;
        padding: 34px 16px 30px;
        border-right: 1px solid var(--kk-divider);
        border-bottom: 1px solid var(--kk-divider);
        background: var(--kk-canvas);
        text-align: center;
        transition: background 200ms var(--kk-ease);
      }

      .tile:hover {
        background: var(--kk-surface);
      }

      /* Inside the tile: its neighbours paint over anything drawn beyond its edges. */
      .tile:focus-visible {
        outline-offset: -3px;
      }

      /* Wide screens trade the phone grid's hairlines for cards, which hold together
         when a room has only one or two devices. */
      :host([wide]) .grid {
        grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
        gap: 18px;
        border-top: none;
        overflow: visible;
      }

      :host([wide]) .tile {
        margin-right: 0;
        padding: 40px 20px 34px;
        border: none;
        border-radius: 20px;
        background: var(--kk-surface);
        box-shadow: var(--kk-shadow);
        transition: transform 220ms var(--kk-ease), background 220ms var(--kk-ease);
      }

      :host([wide]) .tile:hover {
        transform: translateY(-3px);
        background: var(--kk-surface-raised);
      }

      :host([wide]) .art-box {
        width: 150px;
        height: 150px;
      }

      .art-box {
        position: relative;
        width: 132px;
        height: 132px;
      }

      .art-box::before {
        content: "";
        position: absolute;
        inset: 6%;
        border-radius: 50%;
        background: radial-gradient(circle, var(--kk-accent-soft), transparent 68%);
        opacity: 0;
        transition: opacity 500ms var(--kk-ease);
      }

      .running .art-box::before {
        opacity: 1;
        animation: kk-breathe 4.8s ease-in-out infinite;
      }

      .name {
        font-size: 19px;
      }

      .status {
        min-height: 20px;
        font-size: 14px;
        color: var(--kk-text-2);
      }

      .status.active {
        color: var(--kk-accent-text);
      }

      .status.alert {
        color: var(--kk-danger);
      }

      .status.offline {
        color: var(--kk-text-3);
      }

      .badge {
        position: absolute;
        top: 16px;
        right: 16px;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: var(--kk-danger);
        box-shadow: 0 0 0 4px color-mix(in srgb, var(--kk-danger) 20%, transparent);
      }

      /* Controls not yet seen to work on hardware: said quietly, in the corner. */
      .untested {
        position: absolute;
        top: 12px;
        left: 12px;
        padding: 2px 8px;
        border-radius: var(--kk-radius-pill);
        background: var(--kk-control);
        font-size: 11px;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: var(--kk-text-2);
      }

      .empty {
        display: grid;
        justify-items: center;
        gap: 10px;
        padding: 80px 24px;
        text-align: center;
        color: var(--kk-text-2);
      }

      .empty strong {
        font-size: 20px;
        font-weight: 400;
        color: var(--kk-text);
      }

      .tab-body {
        padding-bottom: 48px;
      }

      @keyframes kk-breathe {
        50% {
          transform: scale(1.1);
        }
      }
    `
		];
	}
	open(e) {
		this.ctx.navigate(`/d/${e.id}`);
	}
	renderTile(e) {
		let t = jt(this.hass, e), n = e.untested.length > 0;
		return b`<button
      class="tile ${t.running ? "running" : ""}"
      aria-label=${n ? `${e.name}, ${P("status.untested")}` : e.name}
      aria-describedby="status-${e.id}"
      @click=${() => this.open(e)}
    >
      ${t.tone === "alert" ? b`<span class="badge" aria-hidden="true"></span>` : C}
      ${n ? b`<span class="untested">${P("status.untested")}</span>` : C}
      <span class="art-box">${Z(e.kind, e.model)}</span>
      <span class="name">${e.name}</span>
      <span class="status ${t.tone}" id="status-${e.id}">${t.text}</span>
    </button>`;
	}
	renderDevices() {
		let e = this.data.devices;
		if (!e.length) return b`<div class="empty">
        <strong>${P("app.no_devices")}</strong>
        <span>${P("app.no_devices_body")}</span>
      </div>`;
		let t = /* @__PURE__ */ new Map();
		for (let n of e) {
			let e = n.area ?? P("home.other_area");
			t.set(e, [...t.get(e) ?? [], n]);
		}
		let n = t.size === 1;
		return [...t.entries()].map(([e, t]) => b`
        ${n ? b`<div style="height: 8px"></div>` : b`<h2 class="area eyebrow">${e}</h2>`}
        <div class="grid">${t.map((e) => this.renderTile(e))}</div>
      `);
	}
	renderSummary() {
		let e = this.data.devices.map((e) => jt(this.hass, e)), t = e.filter((e) => e.running).length, n = e.filter((e) => e.tone === "alert").length;
		return n ? b`<span class="warn">${P("home.summary_alert", { count: n })}</span>` : t ? b`<span class="live">${P("home.summary_running", { count: t })}</span>` : b`${P("home.summary_quiet")}`;
	}
	render() {
		let e = this.hass.user?.name?.split(" ")[0], t = [
			{
				key: "devices",
				label: P("nav.devices")
			},
			{
				key: "usage",
				label: P("nav.usage")
			},
			...this.ctx.admin ? [{
				key: "account",
				label: P("nav.account")
			}] : []
		];
		return b`
      <div class="intro">
        <div style="display: flex; align-items: flex-start;">
          ${this.narrow ? b`<button
                class="round menu"
                aria-label=${P("nav.menu")}
                @click=${() => this.dispatchEvent(new CustomEvent("kkp-menu", {
			bubbles: !0,
			composed: !0
		}))}
              >
                ${O("menu")}
              </button>` : C}
          <div>
            <h1 tabindex="-1">${e ? P("home.welcome", { name: e }) : P("home.welcome_anonymous")}</h1>
            <div class="summary">${this.renderSummary()}</div>
          </div>
        </div>
        <a class="help" href=${Xt} target="_blank" rel="noreferrer" aria-label=${P("home.help")}>
          ${O("help")}<span>${P("home.help")}</span>
        </a>
      </div>
      <kkp-tabs
        .items=${t}
        .active=${this.tab}
        @kkp-select=${(e) => this.ctx.navigate(e.detail === "devices" ? "/" : `/${e.detail}`)}
      ></kkp-tabs>
      <div class="tab-body" role="tabpanel" aria-label=${t.find((e) => e.key === this.tab)?.label ?? ""}>
        ${this.tab === "usage" ? b`<kkp-home-usage .hass=${this.hass} .data=${this.data} .ctx=${this.ctx}></kkp-home-usage>` : this.tab === "account" ? b`<kkp-account .hass=${this.hass} .data=${this.data} .ctx=${this.ctx}></kkp-account>` : this.renderDevices()}
      </div>
    `;
	}
}), j("kkp-settings", class extends q {
	static {
		this.properties = {
			...q.properties,
			open: { state: !0 },
			draft: { state: !0 }
		};
	}
	constructor() {
		super(), this.open = null, this.draft = null;
	}
	static {
		this.styles = [
			A,
			R,
			z,
			o`
      :host {
        display: block;
        max-width: 760px;
        padding-bottom: 48px;
      }

      .lock {
        margin-top: 8px;
      }
    `
		];
	}
	toggleOpen(e, t = null) {
		this.open = this.open === e ? null : e, this.draft = t;
	}
	navigate(e) {
		this.ctx.navigate(`/d/${this.device.id}/${e}`);
	}
	async closeEditor(e) {
		this.open = null, await this.updateComplete, this.renderRoot.querySelector(`[data-key="${e}"]`)?.focus();
	}
	async selectOption(e, t) {
		let n = this.entityId(e);
		n && (this.closeEditor(e), await this.queued(e, "select", "select_option", {
			entity_id: n,
			option: t
		}));
	}
	async saveNumber(e, t) {
		let n = this.entityId(e);
		if (n) {
			if (e === "max_temperature") {
				let n = this.number(e);
				if (n === null || t > n) {
					let n = String(this.entity(e)?.attributes.unit_of_measurement ?? this.device.units.temperature);
					if (!await this.ctx.confirm({
						title: P("settings.scald_title"),
						body: P("settings.scald_body", { temperature: Y(t, n) }),
						confirmLabel: P("settings.scald_confirm"),
						destructive: !0
					})) return;
				}
			}
			this.closeEditor(e), await this.queued(e, "number", "set_value", {
				entity_id: n,
				value: t
			});
		}
	}
	async restart() {
		if (await this.ctx.confirm({
			title: P("settings.restart_title", { name: this.device.name }),
			body: P("settings.restart_body"),
			confirmLabel: P("settings.restart_confirm"),
			destructive: !0
		})) try {
			await this.ctx.api.restartValve(this.device.id), this.ctx.toast(P("settings.restart_done"));
		} catch (e) {
			this.ctx.toast(K(e), "error");
		}
	}
	async setAutoRestore(e) {
		try {
			await this.ctx.api.setWarmupAutoRestore(this.device.id, e);
		} catch (e) {
			this.ctx.toast(K(e), "error");
		}
	}
	selectRow(e, t, n = !1) {
		let r = this.entity(e);
		if (!r) return C;
		let i = Array.isArray(r.attributes.options) ? r.attributes.options : [], a = this.open === e;
		return b`${F({
			label: t,
			value: W(this.hass, r),
			chevron: !0,
			expanded: a,
			disabled: n,
			tag: G(this.device, e) ? P("status.untested") : void 0,
			key: e,
			onClick: () => this.toggleOpen(e)
		})}
      ${a ? Wt({
			label: t,
			choices: i,
			current: r.state,
			display: (e) => W(this.hass, r, e),
			onPick: (t) => void this.selectOption(e, t)
		}) : C}`;
	}
	warmupRows() {
		let e = this.entity("warmup");
		if (!e) return C;
		let t = (Array.isArray(e.attributes.options) ? e.attributes.options : []).filter((e) => e !== "Off"), n = e.state !== "Off", r = t.includes("All Outlets") ? "All Outlets" : t[0];
		return b`${F({
			label: P("settings.warmup"),
			description: P("settings.warmup_body"),
			trailing: b`<kkp-toggle
        .checked=${n}
        ?disabled=${!r}
        label=${P("settings.warmup")}
        @change=${(e) => this.selectOption("warmup", e.detail ? r : "Off")}
      ></kkp-toggle>`
		})}
      ${n && t.length > 1 ? Wt({
			label: P("settings.warmup_outlets"),
			choices: t,
			current: e.state,
			display: (e) => e,
			onPick: (t) => void (t !== e.state && this.selectOption("warmup", t))
		}) : C}`;
	}
	numberRow(e, t, n, r = !1) {
		let i = this.entity(e);
		if (!i) return C;
		let a = U(i), o = this.open === e, s = e.includes("temperature"), c = String(i.attributes.unit_of_measurement ?? "");
		return b`${F({
			label: t,
			value: n(a, c),
			chevron: !0,
			expanded: o,
			disabled: r,
			tag: G(this.device, e) ? P("status.untested") : void 0,
			key: e,
			onClick: () => this.toggleOpen(e, a)
		})}
      ${o ? Gt({
			label: t,
			value: a,
			draft: this.draft,
			min: Number(i.attributes.min ?? 0),
			max: Number(i.attributes.max ?? 100),
			step: Number(i.attributes.step ?? 1),
			unit: c,
			variant: s ? "temperature" : "flow",
			onDraft: (e) => this.draft = e,
			onCancel: () => void this.closeEditor(e),
			onSave: (t) => void this.saveNumber(e, t)
		}) : C}`;
	}
	generalGroup(e) {
		return I(P("settings.general"), b`
        ${this.ctx.admin ? F({
			label: P("settings.about"),
			chevron: !0,
			onClick: () => this.navigate("about")
		}) : C}
        ${this.device.capabilities.usage ? F({
			label: P("settings.water_usage"),
			chevron: !0,
			onClick: () => this.navigate("usage")
		}) : C}
        ${this.ctx.admin ? F({
			label: P("settings.diagnostics"),
			chevron: !0,
			onClick: () => this.navigate("diagnostics")
		}) : C}
        ${e && this.ctx.admin ? F({
			label: P("settings.restart"),
			destructive: !0,
			onClick: () => this.restart()
		}) : C}
      `);
	}
	renderValve() {
		let e = X(this.hass, this.device), t = e !== "idle" && e !== "offline", n = (e, t) => Y(e, t || this.device.units.temperature), r = (this.live?.default_shower)?.zones.flatMap((e) => e.outlets.map((t) => this.device.zones.find((t) => t.zone === e.zone)?.outlets.find((e) => e.position === t)?.name)).filter(Boolean).join(", "), i = this.device.capabilities.warmup_auto_restore, a = Object.keys(this.device.roles).filter((e) => e.startsWith("flow:"));
		return b`
      ${t ? b`<div class="lock">${L("info", P("settings.locked_running"))}</div>` : C}
      ${I(P("settings.defaults"), b`
          ${this.numberRow("default_temperature", P("settings.default_temperature"), n, t)}
          ${F({
			label: P("settings.default_outlets"),
			value: r || "—",
			chevron: !0,
			expanded: this.open === "default_outlets",
			onClick: () => this.toggleOpen("default_outlets")
		})}
          ${this.open === "default_outlets" ? b`<div class="editor muted">${P("settings.default_outlets_body")}</div>` : C}
        `, P("settings.defaults_body"))}
      ${I(P("settings.device_control"), b`
          ${this.warmupRows()}
          ${i === void 0 ? C : F({
			label: P("settings.warmup_auto_restore"),
			description: P("settings.warmup_auto_restore_body"),
			trailing: b`<kkp-toggle
                  .checked=${this.has("warmup_auto_restore") ? this.isOn("warmup_auto_restore") : !!i}
                  label=${P("settings.warmup_auto_restore")}
                  @change=${(e) => this.setAutoRestore(e.detail)}
                ></kkp-toggle>`
		})}
          ${this.numberRow("max_temperature", P("settings.max_temperature"), n, t)}
          ${this.selectRow("max_duration", P("settings.max_duration"), t)}
          ${a.map((e) => this.numberRow(e, a.length > 1 ? `${P("settings.flow")} ${e.split(":")[1]}` : P("settings.flow"), (e) => e === null ? "—" : `${Math.round(e)}%`))}
        `)}
      ${this.renderTouchscreen(t)}
      ${this.generalGroup(!0)}
    `;
	}
	renderTouchscreen(e) {
		if (![
			"panel_language",
			"panel_units",
			"wake_screen",
			"display_brightness",
			"haptic_feedback",
			"accessible_display",
			"flow_control",
			"water_saving_mode"
		].some((e) => this.has(e))) return C;
		let t = (e) => e === null ? "—" : `${Math.round(e)}`, n = this.entity("water_saving_mode");
		return I(P("settings.touchscreen"), b`
        ${this.selectRow("panel_language", P("settings.panel_language"), e)}
        ${this.selectRow("panel_units", P("settings.panel_units"), e)}
        ${this.selectRow("wake_screen", P("settings.wake_screen"), e)}
        ${this.numberRow("display_brightness", P("settings.display_brightness"), t, e)}
        ${this.switchRow("haptic_feedback", P("settings.haptic_feedback"), void 0, e)}
        ${this.switchRow("accessible_display", P("settings.accessible_display"), P("settings.accessible_display_body"), e)}
        ${this.switchRow("flow_control", P("settings.flow_control"), P("settings.flow_control_body"), e)}
        ${n ? F({
			label: P("settings.water_saving_mode"),
			description: P("settings.water_saving_mode_body"),
			value: W(this.hass, n)
		}) : C}
      `, P("settings.touchscreen_body"));
	}
	renderFaucet() {
		return b`
      ${I(P("settings.dispense_defaults"), b`
          ${this.numberRow("dispense_amount", P("settings.dispense_amount"), (e, t) => e === null ? "—" : `${e} ${t || this.device.units.dispense || ""}`.trim())}
          ${this.selectRow("preset", P("settings.preset"))}
          ${F({
			label: P("settings.safety_limit"),
			description: P("settings.safety_limit_body"),
			chevron: !0,
			onClick: () => this.ctx.navigate("/account")
		})}
        `)}
      ${this.generalGroup(!1)}
    `;
	}
	renderController() {
		let e = this.device.capabilities.web_url, t = this.entity("max_duration");
		return b`
      ${I(P("settings.device_control"), b`
          ${t ? F({
			label: P("settings.max_duration"),
			value: `${t.state} min`
		}) : C}
          ${e ? F({
			label: P("settings.web_settings"),
			trailing: O("chevronRight"),
			onClick: () => window.open(e, "_blank", "noreferrer")
		}) : C}
        `)}
      ${this.generalGroup(!1)}
    `;
	}
	switchRow(e, t, n, r = !1) {
		let i = this.entity(e);
		return i ? F({
			label: t,
			description: n,
			tag: G(this.device, e) ? P("status.untested") : void 0,
			trailing: b`<kkp-toggle
        .checked=${i.state === "on"}
        ?disabled=${r || i.state === "unavailable"}
        label=${t}
        @change=${(t) => this.roleService(e, "switch", t.detail ? "turn_on" : "turn_off")}
      ></kkp-toggle>`
		}) : C;
	}
	usable(e) {
		let t = this.entity(e);
		return !!t && t.state !== "unavailable";
	}
	scheduleSummary() {
		let { start: e, end: t, repeat: n, days: r, speed: i } = this.entity("fan_schedule")?.attributes ?? {};
		if (typeof e != "string" || typeof t != "string") return P("smart_fan.schedule_body");
		let a = n === "custom" && Array.isArray(r) ? Ct(r) : P(n === "once" ? "smart_fan.once" : "smart_fan.every_day"), o = this.entity("default_fan_speed"), s = typeof i == "string" && o ? W(this.hass, o, i) : null;
		return [
			`${xt(e)}–${xt(t)}`,
			a,
			s
		].filter(Boolean).join(" · ");
	}
	renderSmartFan() {
		let e = this.entity("fan_state")?.state ?? "off", t = e !== "off" && e !== "unknown" && e !== "unavailable", n = this.isOn("light_state"), r = t || n || this.isOn("night_light");
		return b`
      ${r ? L("info", P("smart_fan.locked_running")) : C}
      ${[
			"default_fan_speed",
			"default_fan_timer",
			"default_fan_run_time",
			"default_light_color",
			"default_light_brightness",
			"circadian_lighting",
			"audible_feedback",
			"humidity_sensing",
			"humidity_sensitivity",
			"fan_schedule"
		].some((e) => this.has(e)) ? b`${I(P("settings.defaults"), b`
              ${this.selectRow("default_fan_speed", P("smart_fan.fan_speed"), t)}
              ${this.switchRow("default_fan_timer", P("smart_fan.fan_timer"), P("smart_fan.fan_timer_body"), t)}
              ${this.usable("default_fan_run_time") ? this.numberRow("default_fan_run_time", P("smart_fan.fan_run_time"), (e) => e === null ? "—" : P("account.minutes", { count: e }), t) : C}
              ${this.usable("default_light_color") ? this.selectRow("default_light_color", P("smart_fan.light_color"), n) : C}
              ${this.numberRow("default_light_brightness", P("smart_fan.light_brightness"), (e) => e === null ? "—" : `${e}%`, n)}
              ${this.switchRow("circadian_lighting", P("smart_fan.circadian"), P("smart_fan.circadian_body"), n)}
              ${this.switchRow("audible_feedback", P("smart_fan.audible_feedback"), P("smart_fan.audible_feedback_body"), r)}
            `, P("smart_fan.defaults_body"))}
          ${I(P("smart_fan.automation"), b`
              ${this.switchRow("humidity_sensing", P("smart_fan.humidity"), P("smart_fan.humidity_body"), t)}
              ${this.usable("humidity_sensitivity") ? this.selectRow("humidity_sensitivity", P("smart_fan.sensitivity"), t) : C}
              ${this.switchRow("fan_schedule", P("smart_fan.schedule"), this.scheduleSummary(), t)}
            `)}` : L("info", P("generic.untested_body"))}
      ${this.generalGroup(!1)}
    `;
	}
	render() {
		return this.device.kind === "valve" ? this.renderValve() : this.device.kind === "faucet" ? this.renderFaucet() : this.device.kind === "controller" ? this.renderController() : this.device.kind === "smart_fan" ? this.renderSmartFan() : this.generalGroup(!1);
	}
}), j("kkp-outlet", class extends E {
	static {
		this.properties = {
			name: {},
			kind: {},
			variant: {},
			state: {},
			locked: { type: Boolean },
			disabled: { type: Boolean }
		};
	}
	constructor() {
		super(), this.state = "off", this.variant = null, this.locked = !1, this.disabled = !1;
	}
	static {
		this.styles = [
			A,
			Q,
			o`
      :host {
        display: block;
      }

      button {
        display: grid;
        justify-items: center;
        gap: 12px;
        width: 100%;
      }

      .circle {
        position: relative;
        display: grid;
        place-items: center;
        width: 72px;
        height: 72px;
        border-radius: 50%;
        background: var(--kk-outlet);
        color: var(--kk-text);
        transition: background 220ms var(--kk-ease), color 220ms var(--kk-ease),
          box-shadow 220ms var(--kk-ease), transform 160ms var(--kk-ease);
      }

      button:hover:not(:disabled) .circle {
        background: var(--kk-outlet-hover);
      }

      button:active:not(:disabled) .circle {
        transform: scale(0.95);
      }

      .circle svg.fixture {
        width: 40px;
        height: 40px;
      }

      .staged .circle,
      button.staged:hover:not(:disabled) .circle {
        background: var(--kk-accent-soft);
        color: var(--kk-accent);
        box-shadow: inset 0 0 0 2px var(--kk-accent);
      }

      .on .circle,
      button.on:hover:not(:disabled) .circle {
        background: var(--kk-accent-fill);
        color: var(--kk-accent-on);
      }

      .on .circle::after {
        content: "";
        position: absolute;
        inset: -6px;
        border-radius: 50%;
        border: 2px solid var(--kk-accent);
        opacity: 0;
        animation: kk-pulse 2.4s var(--kk-ease) infinite;
      }

      .on.running .fixture .spray line {
        stroke: currentColor;
      }

      .badge {
        position: absolute;
        right: -2px;
        bottom: -2px;
        display: grid;
        place-items: center;
        width: 26px;
        height: 26px;
        border-radius: 50%;
        background: var(--kk-control);
        color: var(--kk-text-2);
      }

      .badge svg {
        width: 15px;
        height: 15px;
      }

      .name {
        font-size: 17px;
        text-align: center;
        line-height: 1.25;
      }

      .variant {
        margin-top: -8px;
        font-size: 13px;
        color: var(--kk-text-3);
      }

      button:disabled {
        opacity: 0.45;
      }

      button.locked:disabled {
        opacity: 0.75;
      }

      @keyframes kk-pulse {
        0% {
          transform: scale(0.92);
          opacity: 0.7;
        }
        70%,
        100% {
          transform: scale(1.18);
          opacity: 0;
        }
      }
    `
		];
	}
	render() {
		return b`<button
      class=${[
			this.state,
			this.state === "on" ? "running" : "",
			this.locked ? "locked" : ""
		].join(" ")}
      aria-pressed=${this.state === "off" ? "false" : "true"}
      ?disabled=${this.disabled || this.locked}
      @click=${() => this.dispatchEvent(new CustomEvent("kkp-toggle"))}
    >
      <span class="circle">
        ${qe(this.kind)}
        ${this.locked ? b`<span class="badge">${O("lock", P("shower.locked"))}</span>` : C}
      </span>
      <span class="name">${this.name}</span>
      ${this.variant ? b`<span class="variant">${this.variant}</span>` : C}
    </button>`;
	}
});
// src/util/staging.ts
function Zt(e, t) {
	return e === null || !t ? e : Math.min(t.max, Math.max(t.min, Math.round(e)));
}
function Qt(e, t) {
	return e?.staging_bounds?.find((e) => e.zone === t);
}
function $t(e, t, n) {
	return e.zones.map(({ zone: r }) => {
		let i = t?.default_shower?.zones.find((e) => e.zone === r), a = B(n, e, `temperature:${r}`), o = a?.attributes?.unit_of_measurement === e.units.temperature ? U(a) : null;
		return {
			zone: r,
			outlets: i ? [...i.outlets] : [],
			temperature: Zt(i?.temperature ?? o, Qt(t, r))
		};
	});
}
function en(e, t, n) {
	return e.map((e) => {
		if (e.zone !== t) return e;
		let r = e.outlets.includes(n) ? e.outlets.filter((e) => e !== n) : [...e.outlets, n].sort((e, t) => e - t);
		return {
			...e,
			outlets: r
		};
	});
}
function tn(e, t, n, r) {
	return e.map((e) => e.zone === t ? {
		...e,
		temperature: Zt(n, Qt(r, t))
	} : e);
}
function nn(e) {
	return e.some((e) => e.outlets.length > 0);
}
function rn(e) {
	let t = e.find((e) => e.zone === 1);
	return nn(e) && t?.temperature !== null && t !== void 0;
}
function an(e, t, n) {
	let r = { device_id: e };
	for (let e of t) {
		e.temperature !== null && (r[`zone${e.zone}_temperature`] = e.temperature);
		for (let t of e.outlets) r[`zone${e.zone}_outlet_${t}`] = !0;
	}
	return n?.zone1 !== void 0 && n.zone1 !== null && (r.flow = Math.round(n.zone1)), n?.zone2 !== void 0 && n.zone2 !== null && t.some((e) => e.zone === 2) && (r.zone2_flow = Math.round(n.zone2)), r;
}
j("kkp-shower", class extends q {
	static {
		this.properties = {
			...q.properties,
			tab: {},
			wide: { type: Boolean },
			stage: { state: !0 },
			stagedFlow: { state: !0 }
		};
	}
	constructor() {
		super(), this.touched = !1, this.lastStarted = null, this.stage = null, this.stagedFlow = {};
	}
	static {
		this.styles = [
			A,
			z,
			o`
      :host {
        display: block;
      }

      .layout {
        display: grid;
        gap: 20px;
        align-items: start;
      }

      :host([wide]) .layout {
        grid-template-columns: minmax(0, 1.25fr) minmax(320px, 1fr);
        gap: 28px;
      }

      .main,
      .side {
        display: grid;
        gap: 18px;
      }

      .simple-start {
        display: flex;
        align-items: center;
        gap: 20px;
        width: 100%;
        min-height: 84px;
        padding: 16px 24px;
        border-radius: var(--kk-radius-card);
        border: 1.5px solid var(--kk-accent-line);
        background: var(--kk-surface-raised);
        box-shadow: var(--kk-shadow);
        text-align: left;
        transition: border-color 200ms var(--kk-ease), transform 160ms var(--kk-ease);
      }

      .simple-start:hover:not(:disabled) {
        border-color: var(--kk-accent);
      }

      .simple-start:active:not(:disabled) {
        transform: scale(0.99);
      }

      .simple-start:disabled {
        opacity: 0.5;
      }

      .simple-start .play svg {
        width: 34px;
        height: 34px;
        color: var(--kk-accent-fill);
      }

      .ss-title {
        display: block;
        font-size: 26px;
        font-weight: 400;
      }

      .ss-sub {
        display: block;
        margin-top: 4px;
        font-size: 15px;
        color: var(--kk-text-2);
      }

      .zone {
        display: grid;
        gap: 22px;
        padding: 24px;
      }

      .zone-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
      }

      .outlets {
        display: grid;
        gap: 12px;
        grid-template-columns: repeat(var(--count), minmax(0, 1fr));
      }

      .hint {
        margin: -6px 0 0;
        font-size: 14px;
        color: var(--kk-text-3);
        text-align: center;
      }

      .lock-note {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        margin: -6px 0 0;
        font-size: 14px;
        color: var(--kk-text-2);
      }

      .lock-note svg {
        width: 16px;
        height: 16px;
      }

      .divider {
        height: 1px;
        background: var(--kk-divider);
      }

      .start-bar {
        position: sticky;
        bottom: 104px;
        display: flex;
        justify-content: center;
        z-index: 1;
        pointer-events: none;
      }

      :host([wide]) .start-bar {
        bottom: 24px;
      }

      .start-bar .pill {
        min-width: 220px;
        min-height: 56px;
        font-size: 19px;
        pointer-events: auto;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
        animation: kk-rise-in 320ms var(--kk-ease);
      }

      .side h2 {
        margin: 6px 4px 2px;
        font-size: 18px;
        font-weight: 400;
        color: var(--kk-text-2);
      }

      @keyframes kk-rise-in {
        from {
          opacity: 0;
          transform: translateY(12px);
        }
      }
    `
		];
	}
	get phase() {
		return X(this.hass, this.device);
	}
	willUpdate(e) {
		super.willUpdate(e), this.phase === "idle" ? this.stage && this.touched || (this.stage = $t(this.device, this.live, this.hass)) : (this.stage = null, this.touched = !1);
	}
	focusFallback() {
		return this.renderRoot.querySelector("kkp-running, .simple-start");
	}
	defaultSummary() {
		let e = this.live?.default_shower;
		if (!e) return "";
		if (e.refused_because) return P("shower.simple_start_unknown");
		let t = e.zones.flatMap((e) => e.outlets.map((t) => this.device.zones.find((t) => t.zone === e.zone)?.outlets.find((e) => e.position === t)?.name)), n = e.zones[0]?.temperature ?? null;
		return [t.filter(Boolean).join(", "), Y(n, this.device.units.temperature)].filter((e) => e && e !== "—").join(" · ");
	}
	simpleStart() {
		let e = this.entityId("shower");
		e && this.queued("shower", "switch", "turn_on", { entity_id: e });
	}
	stop() {
		let e = this.entityId("shower");
		e && this.stopNow("switch", "turn_off", { entity_id: e });
	}
	sendShower(e) {
		let t = {
			zone1: this.stagedFlow[1] ?? this.number("flow:1"),
			zone2: this.stagedFlow[2] ?? this.number("flow:2")
		};
		this.lastStarted = e, this.queued("shower", "kohler_konnect", "custom_shower", an(this.device.id, e, t));
	}
	resume() {
		let e = this.device.zones.map((e) => ({
			zone: e.zone,
			outlets: e.outlets.filter((t) => this.isOn(`outlet:${e.zone}:${t.position}`)).map((e) => e.position),
			temperature: this.number(`temperature:${e.zone}`)
		})), t = this.lastStarted ?? (nn(e) ? e : $t(this.device, this.live, this.hass));
		this.sendShower(t);
	}
	toggleOutlet(e, t) {
		if (this.phase === "idle") {
			this.touched = !0, this.stage = en(this.stage ?? [], e, t.position);
			return;
		}
		let n = `outlet:${e}:${t.position}`, r = this.entityId(n);
		r && this.queued(n, "switch", this.isOn(n) ? "turn_off" : "turn_on", { entity_id: r });
	}
	setTemperature(e, t) {
		if (this.phase === "idle") {
			this.touched = !0, this.stage = tn(this.stage ?? [], e, t, this.live);
			return;
		}
		let n = this.entityId(`temperature:${e}`);
		n && this.queued(`temperature:${e}`, "number", "set_value", {
			entity_id: n,
			value: t
		});
	}
	setFlow(e, t) {
		if (this.phase === "idle") {
			this.stagedFlow = {
				...this.stagedFlow,
				[e]: t
			};
			return;
		}
		let n = this.entityId(`flow:${e}`);
		n && this.queued(`flow:${e}`, "number", "set_value", {
			entity_id: n,
			value: t
		});
	}
	renderZone(e, t) {
		let n = this.phase, r = n === "idle", i = n === "warming", a = this.stage?.find((t) => t.zone === e.zone), o = this.entity(`temperature:${e.zone}`), s = this.live?.staging_bounds?.find((t) => t.zone === e.zone), c = Number(o?.attributes.min ?? 58), l = Number(o?.attributes.max ?? 118), u = this.entity(`flow:${e.zone}`), ee = !!u && u.attributes.flow_control_available !== !1, te = r ? a?.temperature ?? null : U(o), ne = r ? this.device.units.temperature : String(o?.attributes.unit_of_measurement ?? this.device.units.temperature);
		return b`<section class="card zone" aria-labelledby="zone-${e.zone}">
      <div class="zone-head">
        <h2 class="section-label" id="zone-${e.zone}">
          ${t ? P("shower.zone", { zone: e.zone }) : P("shower.outlets")}
        </h2>
      </div>
      <div class="outlets" style="--count: ${Math.max(e.outlets.length, 1)}">
        ${e.outlets.map((t) => {
			let o = `outlet:${e.zone}:${t.position}`, s = r ? a?.outlets.includes(t.position) ? "staged" : "off" : H(this.entity(o)) ? "on" : "off";
			return b`<kkp-outlet
            .name=${t.name}
            .kind=${Ge(t.code, t.name)}
            .variant=${t.variant}
            .state=${s}
            ?locked=${i}
            ?disabled=${n === "offline" || !r && !this.has(o)}
            @kkp-toggle=${() => this.toggleOutlet(e.zone, t)}
          ></kkp-outlet>`;
		})}
      </div>
      ${i ? b`<p class="lock-note">${O("lock")}${P("shower.locked_warming")}</p>` : r && !a?.outlets.length ? b`<p class="hint">${P("shower.staged_hint")}</p>` : C}
      <div class="divider"></div>
      ${o || r ? b`<kkp-slider
            label=${P("shower.temperature")}
            .value=${te}
            .min=${r ? s?.min ?? (ne === "°C" ? 15 : 59) : c}
            .max=${r ? s?.max ?? (ne === "°C" ? 47 : 118) : l}
            .step=${Number(o?.attributes.step ?? 1)}
            unit=${ne}
            .coldValue=${r ? null : c}
            ?disabled=${i || n === "offline"}
            @change=${(t) => this.setTemperature(e.zone, t.detail)}
          ></kkp-slider>` : C}
      ${ee ? b`<kkp-slider
            compact
            variant="flow"
            label=${P("shower.flow")}
            .value=${r ? this.stagedFlow[e.zone] ?? U(u) : U(u)}
            .min=${Number(u.attributes.min ?? 8)}
            .max=${Number(u.attributes.max ?? 100)}
            .step=${1}
            ?disabled=${i || n === "offline"}
            @change=${(t) => this.setFlow(e.zone, t.detail)}
          ></kkp-slider>` : C}
    </section>`;
	}
	renderHero() {
		let e = this.phase;
		if (e === "idle" || e === "offline") {
			let t = this.defaultSummary();
			return b`<button
        class="simple-start"
        ?disabled=${e === "offline" || !this.has("shower")}
        @click=${this.simpleStart}
      >
        <span class="play">${O("play")}</span>
        <span>
          <span class="ss-title">${P("shower.simple_start")}</span>
          ${t ? b`<span class="ss-sub">${t}</span>` : C}
        </span>
      </button>`;
		}
		let t = this.entity("status"), n = this.entity("temperature:1"), r = U(n), i = String(n?.attributes.unit_of_measurement ?? this.device.units.temperature), a = parseInt(this.entity("max_duration")?.state ?? "", 10), o = t?.attributes.seconds_remaining;
		return b`<kkp-running
      .phase=${e}
      .temperature=${r}
      .setpoint=${r}
      .unit=${i}
      .tint=${Dt(r, i)}
      .atTemperature=${this.isOn("at_temperature")}
      .secondsRemaining=${typeof o == "number" ? o : null}
      .totalSeconds=${Number.isFinite(a) ? a * 60 : null}
      @kkp-stop=${this.stop}
      @kkp-resume=${this.resume}
    ></kkp-running>`;
	}
	renderControls() {
		let e = this.device.zones.length > 1, t = this.phase === "idle", n = this.stage ?? [];
		return b`
      ${this.phase === "offline" ? L("warning", P("status.offline")) : C}
      ${this.renderHero()}
      ${this.device.zones.map((t) => this.renderZone(t, e))}
      ${t && nn(n) ? b`<div class="start-bar">
            <button class="pill filled" ?disabled=${!rn(n)} @click=${() => this.sendShower(n)}>
              ${O("play")}${P("shower.start")}
            </button>
          </div>` : C}
    `;
	}
	scenes(e, t = !1) {
		return b`<kkp-scenes
      .hass=${this.hass}
      .device=${this.device}
      .live=${this.live}
      .ctx=${this.ctx}
      .role=${e}
      ?compact=${t}
    ></kkp-scenes>`;
	}
	render() {
		return b`${this.statusRegion(At(this.phase))}${this.renderTab()}`;
	}
	renderTab() {
		if (this.wide) {
			let e = this.has("favorite"), t = ft(this.entity("experience")).length > 0;
			return b`<div class="layout">
        <div class="main">${this.renderControls()}</div>
        <div class="side">
          ${e ? b`<h2>${P("nav.favorites")}</h2>${this.scenes("favorite", !0)}` : C}
          ${t ? b`<h2>${P("nav.experiences")}</h2>${this.scenes("experience", !0)}` : C}
        </div>
      </div>`;
		}
		return this.tab === "favorites" ? this.scenes("favorite") : this.tab === "experiences" ? this.scenes("experience") : b`<div class="layout"><div class="main">${this.renderControls()}</div></div>`;
	}
});
// src/views/smart-fan.ts
var on = /* @__PURE__ */ new Set([
	"off",
	"unavailable",
	"unknown"
]);
j("kkp-smart-fan", class extends q {
	static {
		this.properties = { ...q.properties };
	}
	static {
		this.styles = [
			A,
			z,
			Q,
			o`
      :host {
        display: grid;
        gap: 24px;
        max-width: 760px;
        padding-bottom: 48px;
      }

      .hero {
        display: grid;
        justify-items: center;
        gap: 12px;
        padding-top: 8px;
      }

      .art-box {
        width: 168px;
        height: 168px;
      }

      .now {
        font-size: 20px;
        color: var(--kk-text-2);
        text-align: center;
      }

      .parts {
        display: flex;
        justify-content: center;
        gap: 56px;
      }

      .part {
        display: grid;
        justify-items: center;
        gap: 10px;
        width: 128px;
        text-align: center;
      }

      .part .circle {
        display: grid;
        place-items: center;
        width: 76px;
        height: 76px;
        border-radius: 50%;
        background: var(--kk-control);
        color: var(--kk-text);
        transition: background 220ms var(--kk-ease), transform 160ms var(--kk-ease);
      }

      .part .circle svg {
        width: 34px;
        height: 34px;
      }

      button.part[aria-pressed="true"] .circle {
        background: var(--kk-accent-fill);
        color: var(--kk-accent-on);
      }

      button.part:active .circle {
        transform: scale(0.95);
      }

      .part .name {
        font-size: 18px;
      }

      .part .detail {
        min-height: 18px;
        font-size: 14px;
        color: var(--kk-text-2);
      }

      .favorites {
        display: grid;
        gap: 10px;
      }

      .favorites h2 {
        margin: 0;
        font-size: 17px;
        font-weight: 500;
      }

      .chips {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }

      .chip {
        padding: 10px 16px;
        border-radius: var(--kk-radius-pill);
        background: var(--kk-control);
        font-size: 15px;
      }

      .chip[aria-pressed="true"] {
        background: var(--kk-accent-fill);
        color: var(--kk-accent-on);
      }

      .actions {
        display: flex;
        justify-content: center;
      }
    `
		];
	}
	get fanRunning() {
		return !on.has(this.entity("fan_state")?.state ?? "off");
	}
	get lightOn() {
		return this.isOn("light_state");
	}
	fanDetail() {
		let e = this.entity("fan_state");
		if (!e || !V(e)) return "—";
		let t = e.attributes.favorite;
		return [W(this.hass, e), t].filter(Boolean).join(" · ");
	}
	lightDetail() {
		let e = this.entity("light_state");
		if (!e || !V(e)) return "—";
		if (e.state !== "on") return P("status.off");
		let t = e.attributes.brightness, n = e.attributes.favorite;
		return [t ? `${t}%` : P("smart_fan.on"), n].filter(Boolean).join(" · ");
	}
	now() {
		let e = [];
		return this.fanRunning && e.push(P("smart_fan.fan_on")), this.lightOn && e.push(P("smart_fan.light_on")), e.length ? e.join(" · ") : P("status.off");
	}
	renderPart(e, t, n, r) {
		let i = this.entity(e), a = O(e === "fan" ? "fan" : "light");
		return !i || !V(i) ? b`<div class="part">
        <span class="circle" aria-hidden="true">${a}</span>
        <span class="name">${t}</span><span class="detail">${n}</span>
      </div>` : b`<button
      class="part"
      aria-pressed=${String(r)}
      @click=${() => this.roleService(e, e, r ? "turn_off" : "turn_on")}
    >
      <span class="circle">${a}</span>
      <span class="name">${t}</span><span class="detail">${n}</span>
    </button>`;
	}
	favorites(e, t) {
		if (!t || !V(t)) return C;
		let n = e === "fan", r = t.attributes[n ? "preset_modes" : "effect_list"] ?? [];
		if (!r.length) return C;
		let i = t.state === "on" ? t.attributes[n ? "preset_mode" : "effect"] : null;
		return b`<section class="favorites">
      <h2>${P(n ? "smart_fan.fan_favorites" : "smart_fan.light_favorites")}</h2>
      <div class="chips">
        ${r.map((t) => b`<button
            class="chip"
            aria-pressed=${String(t === i)}
            @click=${() => t === i ? this.roleService(e, e, "turn_off") : n ? this.roleService("fan", "fan", "set_preset_mode", { preset_mode: t }) : this.roleService("light", "light", "turn_on", { effect: t })}
          >
            ${t}
          </button>`)}
      </div>
    </section>`;
	}
	focusFallback() {
		return this.renderRoot.querySelector("button.part");
	}
	render() {
		let e = this.has("light_state") || this.has("light"), t = [
			"fan",
			"light",
			"stop_all"
		].some((e) => this.has(e)), n = this.fanRunning || this.lightOn;
		return b`
      ${this.statusRegion(this.now())}
      <div class="hero ${this.fanRunning ? "running" : ""}">
        <div class="art-box">${Z("smart_fan")}</div>
        <div class="now">${this.now()}</div>
      </div>
      ${!t && this.device.untested.length ? L("info", P("generic.untested_body")) : C}
      <div class="parts">
        ${this.renderPart("fan", P("smart_fan.fan"), this.fanDetail(), this.fanRunning)}
        ${e ? this.renderPart("light", P("smart_fan.light"), this.lightDetail(), this.lightOn) : C}
      </div>
      ${this.favorites("fan", this.entity("fan"))}
      ${e ? this.favorites("light", this.entity("light")) : C}
      ${n && this.has("stop_all") ? b`<div class="actions">
            <button class="pill" @click=${() => this.roleService("stop_all", "button", "press")}>
              ${O("stop")}${P("smart_fan.stop_all")}
            </button>
          </div>` : C}
    `;
	}
});
// src/components/bar-chart.ts
var sn = [
	1,
	1.5,
	2,
	2.5,
	3,
	4,
	5,
	6,
	8,
	10
];
function cn(e, t = 4) {
	if (!(e > 0)) return {
		top: t,
		step: 1
	};
	let n = e / t, r = 10 ** Math.floor(Math.log10(n)), i = (sn.find((e) => n / r <= e) ?? 10) * r;
	return {
		top: i * t,
		step: i
	};
}
var ln = 0, un = class extends E {
	static {
		this.properties = {
			bars: { attribute: !1 },
			ticks: { attribute: !1 },
			caption: {},
			width: { state: !0 },
			selected: { state: !0 }
		};
	}
	constructor() {
		super(), this.gradient = `kk-bars-${++ln}`, this.onWindowKey = (e) => {
			e.key === "Escape" && (this.selected = null);
		}, this.bars = [], this.ticks = [], this.width = 0, this.selected = null;
	}
	static {
		this.styles = [A, o`
      :host {
        display: block;
        position: relative;
      }

      svg {
        display: block;
        width: 100%;
        height: 260px;
        overflow: visible;
      }

      svg:focus-visible {
        outline: 2px solid var(--kk-accent);
        outline-offset: 4px;
        border-radius: 8px;
      }

      .grid {
        stroke: var(--kk-grid);
        stroke-width: 1;
      }

      .track {
        fill: var(--kk-bar-track);
      }

      .bar {
        transition: opacity 200ms var(--kk-ease);
      }

      .dim .bar {
        opacity: 0.45;
      }

      .dim .bar.selected {
        opacity: 1;
      }

      text {
        fill: var(--kk-text-3);
        font-size: 12px;
        font-family: var(--kk-font);
      }

      .hit {
        fill: transparent;
        cursor: pointer;
      }

      .tip {
        position: absolute;
        transform: translate(-50%, calc(-100% - 10px));
        padding: 8px 12px;
        border-radius: 10px;
        background: var(--kk-control);
        color: var(--kk-text);
        font-size: 14px;
        white-space: nowrap;
        pointer-events: none;
        box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25);
      }
    `];
	}
	connectedCallback() {
		super.connectedCallback(), this.observer = new ResizeObserver((e) => {
			this.width = Math.round(e[0].contentRect.width);
		}), this.observer.observe(this);
	}
	disconnectedCallback() {
		super.disconnectedCallback(), this.observer?.disconnect(), window.removeEventListener("keydown", this.onWindowKey);
	}
	updated(e) {
		e.has("selected") && (window.removeEventListener("keydown", this.onWindowKey), this.selected !== null && window.addEventListener("keydown", this.onWindowKey));
	}
	willUpdate(e) {
		e.has("bars") && (this.selected = null);
	}
	onKey(e) {
		if (!this.bars.length) return;
		let t = this.bars.length - 1;
		if (e.key === "ArrowRight") this.selected = Math.min(t, (this.selected ?? -1) + 1);
		else if (e.key === "ArrowLeft") this.selected = Math.max(0, (this.selected ?? t + 1) - 1);
		else if (e.key === "Escape") this.selected = null;
		else return;
		e.preventDefault();
	}
	render() {
		let e = Math.max(this.width, 240), t = e - 44, n = cn(Math.max(0, ...this.bars.map((e) => e.value ?? 0))), r = t / Math.max(this.bars.length, 1), i = Math.max(3, Math.min(16, r * .42)), a = (e) => 14 + 216 * (1 - e / n.top), o = Array.from({ length: 5 }, (e, t) => t * n.step), s = this.selected, c = s === null ? void 0 : this.bars[s], l = (e) => r * (e + .5);
		return b`
      <svg
        viewBox="0 0 ${e} ${260}"
        tabindex="0"
        role="img"
        aria-label=${this.caption}
        class=${s === null ? "" : "dim"}
        @keydown=${this.onKey}
        @pointerleave=${() => this.selected = null}
      >
        <defs>
          <linearGradient id=${this.gradient} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" style="stop-color: var(--kk-bar-top)" />
            <stop offset="1" style="stop-color: var(--kk-bar-bottom)" />
          </linearGradient>
        </defs>
        ${o.map((e) => x`
            <line class="grid" x1="0" x2=${t} y1=${a(e)} y2=${a(e)} />
            <text x=${t + 10} y=${a(e) + 4}>${dn(e)}</text>`)}
        ${this.bars.map((e, t) => {
			let a = e.value ? Math.max(2, 216 * (e.value / n.top)) : 0;
			return x`
            <rect class="track" x=${l(t) - i / 2} y=${14} width=${i} height=${216}
              rx=${i / 2} />
            ${a ? x`<rect class="bar ${t === s ? "selected" : ""}" x=${l(t) - i / 2}
                  y=${230 - a} width=${i} height=${a} rx=${Math.min(i / 2, a / 2)}
                  fill="url(#${this.gradient})" />` : C}
            <rect class="hit" x=${r * t} y="0" width=${r} height=${260}
              @pointerenter=${() => this.selected = t} @click=${() => this.selected = t} />`;
		})}
        ${this.ticks.map((e) => x`<text x=${l(e.index)} y=${252} text-anchor="middle">${e.label}</text>`)}
      </svg>
      ${c ? b`<div
            class="tip"
            style="left: ${l(s) / e * 100}%; top: ${a(c.value ?? 0) / 260 * 260}px"
          >
            ${c.tooltip}
          </div>` : C}
      <div class="visually-hidden" aria-live="polite">${c?.tooltip ?? ""}</div>
      <div class="visually-hidden">
        <table>
          <caption>${this.caption}</caption>
          ${this.bars.map((e) => b`<tr><th scope="row">${e.label}</th><td>${e.tooltip}</td></tr>`)}
        </table>
      </div>
    `;
	}
};
function dn(e) {
	return Number.isInteger(e) ? String(e) : e.toFixed(1);
}
j("kkp-bar-chart", un);
// src/util/usage.ts
function $(e) {
	let [t, n, r] = e.split("-").map(Number);
	return new Date(t, (n || 1) - 1, r || 1);
}
function fn(e, t) {
	return t === "volume" ? e.volume : e.minutes;
}
function pn(e, t, n) {
	return t === "volume" ? mt(e, n) : ht(e);
}
function mn(e, t) {
	let n = e.period === "year", r = new Intl.DateTimeFormat(N(), n ? { month: "long" } : {
		weekday: "short",
		month: "short",
		day: "numeric"
	});
	return e.buckets.map((n) => {
		let i = r.format($(n.key)), a = fn(n, t);
		return {
			key: n.key,
			label: i,
			value: a,
			tooltip: `${i} · ${pn(a ?? 0, t, e.unit)}`
		};
	});
}
function hn(e) {
	let t = e.buckets;
	if (e.period === "week") {
		let e = new Intl.DateTimeFormat(N(), { weekday: "narrow" });
		return t.map((t, n) => ({
			index: n,
			label: e.format($(t.key))
		}));
	}
	if (e.period === "year") {
		let e = new Intl.DateTimeFormat(N(), { month: "narrow" });
		return t.map((t, n) => ({
			index: n,
			label: e.format($(t.key))
		}));
	}
	let n = new Intl.DateTimeFormat(N(), {
		month: "numeric",
		day: "numeric"
	}), r = t.length - 1;
	return [
		0,
		Math.floor(r / 2),
		r
	].filter((e, t, n) => e >= 0 && n.indexOf(e) === t).map((e) => ({
		index: e,
		label: n.format($(t[e].key))
	}));
}
function gn(e) {
	let t = $(e.start), n = $(e.end);
	if (e.period === "year") return String(t.getFullYear());
	if (e.period === "month") return new Intl.DateTimeFormat(N(), {
		month: "short",
		year: "numeric"
	}).format(t);
	let r = new Intl.DateTimeFormat(N(), {
		month: "short",
		day: "numeric",
		year: "numeric"
	});
	return r.formatRange ? r.formatRange(t, n) : `${r.format(t)} – ${r.format(n)}`;
}
var _n = [
	"monday",
	"tuesday",
	"wednesday",
	"thursday",
	"friday",
	"saturday",
	"sunday"
], vn = /* @__PURE__ */ new Set([
	"US",
	"CA",
	"MX",
	"BR",
	"JP",
	"IL"
]);
function yn(e) {
	let t = _n.indexOf(e?.first_weekday ?? "");
	if (t >= 0) return t;
	try {
		let t = new Intl.Locale(e?.language || "en"), n = t.getWeekInfo?.().firstDay ?? t.weekInfo?.firstDay;
		return n ? n - 1 : vn.has(t.maximize().region ?? "") ? 6 : 0;
	} catch {
		return 0;
	}
}
function bn(e, t) {
	if (!e) return null;
	let n = e / (t.toLowerCase().startsWith("gal") ? 40 : 150);
	return n >= 1 ? Math.round(n) : null;
}
j("kkp-usage", class extends q {
	static {
		this.properties = {
			...q.properties,
			period: { state: !0 },
			anchor: { state: !0 },
			metric: { state: !0 },
			history: { state: !0 },
			loading: { state: !0 },
			failed: { state: !0 }
		};
	}
	constructor() {
		super(), this.request = 0, this.period = "month", this.anchor = void 0, this.metric = "volume", this.history = null, this.loading = !1, this.failed = null;
	}
	static {
		this.styles = [A, o`
      :host {
        display: block;
        max-width: 860px;
        padding-bottom: 48px;
      }

      .pager {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 28px;
        margin: 26px 0 22px;
        font-size: 24px;
        font-weight: 300;
      }

      .pager button {
        display: grid;
        place-items: center;
        width: 40px;
        height: 40px;
        border-radius: 50%;
        color: var(--kk-text);
      }

      .pager button:disabled {
        color: var(--kk-text-3);
        opacity: 0.5;
      }

      .pager button svg {
        width: 26px;
        height: 26px;
      }

      .stats {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        text-align: center;
        margin-bottom: 18px;
      }

      .stat span {
        display: block;
        font-size: 17px;
        color: var(--kk-text-2);
        margin-bottom: 6px;
      }

      .stat strong {
        font-size: clamp(24px, 5vw, 32px);
        font-weight: 300;
        font-variant-numeric: tabular-nums;
      }

      .stat small {
        display: block;
        margin-top: 2px;
        font-size: 13px;
        color: var(--kk-text-3);
      }

      .metric {
        display: flex;
        justify-content: center;
        margin: 6px 0 20px;
      }

      .segmented {
        display: inline-flex;
        padding: 4px;
        border-radius: var(--kk-radius-pill);
        background: var(--kk-control);
      }

      .segmented button {
        padding: 8px 20px;
        border-radius: var(--kk-radius-pill);
        font-size: 15px;
        color: var(--kk-text-2);
      }

      .segmented button[aria-pressed="true"] {
        background: var(--kk-canvas);
        color: var(--kk-text);
        box-shadow: inset 0 0 0 1.5px var(--kk-text-2);
      }

      .chart {
        position: relative;
        padding: 12px 0 0;
      }

      .chart.loading {
        opacity: 0.4;
        transition: opacity 300ms;
      }

      .footnote,
      .equivalent {
        margin: 18px 4px 0;
        font-size: 14px;
        color: var(--kk-text-3);
      }

      .equivalent {
        display: flex;
        align-items: center;
        gap: 8px;
        color: var(--kk-text-2);
        font-size: 15px;
      }

      .equivalent svg {
        width: 20px;
        height: 20px;
        color: var(--kk-accent);
      }

      .error {
        display: grid;
        justify-items: center;
        gap: 14px;
        padding: 60px 0;
        color: var(--kk-text-2);
      }
    `];
	}
	firstUpdated() {
		this.load();
	}
	async load() {
		let e = ++this.request;
		this.loading = !0, this.failed = null;
		try {
			let t = await this.ctx.api.usageHistory(this.device.id, this.period, yn(this.hass.locale), this.anchor);
			e === this.request && (this.history = t);
		} catch (t) {
			e === this.request && (this.failed = K(t));
		} finally {
			e === this.request && (this.loading = !1);
		}
	}
	choosePeriod(e) {
		e !== this.period && (this.period = e, this.anchor = void 0, this.history = null, this.load());
	}
	page(e) {
		e && !this.loading && (this.anchor = e, this.load());
	}
	render() {
		let e = this.history, t = e?.unit ?? this.device.units.volume, n = (e) => pn(e ?? null, this.metric, t), r = (e) => e ? this.metric === "volume" ? e.volume : e.minutes : null, i = this.metric === "volume" && this.period !== "week" ? bn(e?.total.volume ?? null, t) : null;
		return b`
      <kkp-tabs
        .items=${[
			{
				key: "week",
				label: P("usage.week")
			},
			{
				key: "month",
				label: P("usage.month")
			},
			{
				key: "year",
				label: P("usage.year")
			}
		]}
        .active=${this.period}
        @kkp-select=${(e) => this.choosePeriod(e.detail)}
      ></kkp-tabs>
      <div role="tabpanel" aria-label=${P(`usage.${this.period}`)}>
      <div class="pager">
        <button aria-label=${P("usage.previous")} ?disabled=${!e?.previous} @click=${() => this.page(e?.previous ?? null)}>
          ${O("back")}
        </button>
        <span aria-live="polite">${e ? gn(e) : "—"}</span>
        <button aria-label=${P("usage.next")} ?disabled=${!e?.next} @click=${() => this.page(e?.next ?? null)}>
          ${O("chevronRight")}
        </button>
      </div>
      <div class="stats">
        <div class="stat"><span>${P("usage.average")}</span><strong>${n(r(e?.average))}</strong><small>${P("usage.per_day")}</small></div>
        <div class="stat"><span>${P("usage.high")}</span><strong>${n(r(e?.high))}</strong></div>
        <div class="stat"><span>${P("usage.total")}</span><strong>${n(r(e?.total))}</strong></div>
      </div>
      <div class="metric">
        <div class="segmented" role="group">
          <button aria-pressed=${String(this.metric === "volume")} @click=${() => this.metric = "volume"}>${P("usage.water")}</button>
          <button aria-pressed=${String(this.metric === "minutes")} @click=${() => this.metric = "minutes"}>${P("usage.time")}</button>
        </div>
      </div>
      ${this.failed ? b`<div class="error" role="alert">
            <span>${P("usage.error")}</span>
            <button class="pill" @click=${() => this.load()}>${P("usage.retry")}</button>
          </div>` : b`<div class="chart ${this.loading ? "loading" : ""}" aria-busy=${String(this.loading)}>
            <kkp-bar-chart
              .bars=${e ? mn(e, this.metric) : []}
              .ticks=${e ? hn(e) : []}
              caption=${`${P("usage.title")} · ${e ? gn(e) : ""}`}
            ></kkp-bar-chart>
          </div>`}
      ${i && i >= 2 ? b`<div class="equivalent">${O("water")}${P("usage.equivalent_bath", { count: i })}</div>` : C}
      ${this.period === "week" ? b`<p class="footnote">${P("usage.calendar_week")}</p>` : C}
      </div>
    `;
	}
});
// src/kohler-konnect-panel.ts
var xn = 980;
function Sn(e) {
	let t = e.split("/").filter(Boolean);
	if (t[0] === "d" && t[1]) {
		let e = t[2] ?? "controls";
		return {
			deviceId: t[1],
			screen: e
		};
	}
	return t[0] === "usage" || t[0] === "account" ? { home: t[0] } : { home: "devices" };
}
var Cn = class extends E {
	static {
		this.properties = {
			hass: { attribute: !1 },
			narrow: { type: Boolean },
			route: { attribute: !1 },
			panel: { attribute: !1 },
			data: { state: !0 },
			live: { state: !0 },
			wide: { state: !0 },
			toastState: { state: !0 },
			dialog: { state: !0 },
			announcement: { state: !0 }
		};
	}
	constructor() {
		super(), this.api = new Ue(() => this.hass), this.navigate = (e) => {
			if (this.navigateImpl) {
				this.navigateImpl(e);
				return;
			}
			let t = this.route?.prefix ?? "/kohler-konnect";
			history.pushState(null, "", e === "/" ? t : `${t}${e}`), window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: !1 } })), window.scrollTo({ top: 0 });
		}, this.toast = (e, t = "info") => {
			this.toastState = {
				message: e,
				kind: t,
				id: Date.now()
			}, this.dismissLater(t);
		}, this.confirm = (e) => new Promise((t) => {
			this.dialog = {
				...e,
				resolve: t
			};
		}), this.narrow = !1, this.route = {
			prefix: "/kohler-konnect",
			path: ""
		}, this.data = null, this.live = {}, this.wide = !1, this.toastState = null, this.dialog = null, this.announcement = "";
	}
	static {
		this.styles = [
			ct,
			A,
			o`
      :host {
        display: block;
        min-height: 100vh;
        background: var(--kk-canvas);
        color: var(--kk-text);
      }

      .page {
        max-width: 1240px;
        margin: 0 auto;
        padding: 0 16px;
      }

      :host([wide]) .page {
        padding: 0 40px;
      }

      /* A full-height column, so the capsule tab bar rests on the bottom edge even when
         the screen's content is short. */
      .device-screen {
        display: flex;
        flex-direction: column;
        min-height: 100vh;
      }

      .device-screen > .page {
        flex: 1;
        width: 100%;
      }

      .device-head {
        position: sticky;
        top: 0;
        z-index: 3;
        background: linear-gradient(var(--kk-canvas) 78%, transparent);
      }

      .device-body {
        padding: 8px 0 24px;
      }

      .loading {
        display: grid;
        place-items: center;
        min-height: 60vh;
        color: var(--kk-text-2);
        font-size: 17px;
        text-align: center;
        gap: 16px;
      }

      .spinner {
        width: 34px;
        height: 34px;
        border-radius: 50%;
        border: 3px solid var(--kk-control);
        border-top-color: var(--kk-accent);
        animation: kk-spin 900ms linear infinite;
      }

      .stale {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 16px;
        padding: 10px 16px;
        background: var(--kk-accent-soft);
        color: var(--kk-text);
        font-size: 15px;
      }

      .stale button {
        color: var(--kk-accent-text);
        font-weight: 500;
      }

      .toast {
        position: fixed;
        left: 50%;
        bottom: 28px;
        z-index: 10;
        display: flex;
        align-items: center;
        gap: 12px;
        max-width: min(560px, calc(100vw - 32px));
        padding: 14px 20px;
        border-radius: 14px;
        background: var(--kk-control);
        color: var(--kk-text);
        font-size: 15px;
        line-height: 1.4;
        box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
        transform: translateX(-50%);
        animation: kk-toast-in 260ms var(--kk-ease);
      }

      .toast.error {
        box-shadow: inset 4px 0 0 var(--kk-danger), 0 12px 40px rgba(0, 0, 0, 0.45);
      }

      .toast svg {
        width: 22px;
        height: 22px;
        flex: none;
      }

      .toast.error svg {
        color: var(--kk-danger);
      }

      @keyframes kk-spin {
        to {
          transform: rotate(1turn);
        }
      }

      @keyframes kk-toast-in {
        from {
          opacity: 0;
          transform: translate(-50%, 12px);
        }
      }
    `
		];
	}
	connectedCallback() {
		super.connectedCallback(), this.observer = new ResizeObserver((e) => {
			let t = e[0].contentRect.width >= xn;
			t !== this.wide && (this.wide = t);
		}), this.observer.observe(this), this.hass && !this.unsubscribeDevices && this.subscribe();
	}
	disconnectedCallback() {
		super.disconnectedCallback(), this.observer?.disconnect(), this.unsubscribeDevices?.then((e) => e()), this.unsubscribeDevices = void 0, this.dropLive();
	}
	willUpdate(e) {
		e.has("hass") && this.hass && (ot(this.hass.locale?.language ?? this.hass.language), this.lang = st(), this.setAttribute("theme", this.hass.themes?.darkMode === !1 ? "light" : "dark"), !this.unsubscribeDevices && this.isConnected && this.subscribe()), this.toggleAttribute("wide", this.wide), (e.has("route") || e.has("hass")) && this.followDevice();
	}
	updated(e) {
		let t = e.get("route");
		t && t.path !== this.route?.path && this.afterNavigation();
	}
	afterNavigation() {
		requestAnimationFrame(() => {
			let e = this.shadowRoot?.activeElement;
			e ? e.localName === "kkp-header" && (this.announcement = e.heading ?? "") : this.renderRoot.querySelector("kkp-header, kkp-home")?.focusHeading?.();
		});
	}
	subscribe() {
		this.unsubscribeDevices = this.api.subscribeDevices((e) => {
			this.data = e;
		}), this.unsubscribeDevices.catch((e) => this.toast(String(e?.message ?? e), "error"));
	}
	followDevice() {
		let { deviceId: e } = Sn(this.route?.path ?? "");
		this.liveSubscription?.deviceId !== e && (this.dropLive(), e && this.hass && (this.liveSubscription = {
			deviceId: e,
			unsubscribe: this.api.subscribeDevice(e, (t) => {
				this.live = {
					...this.live,
					[e]: t
				};
			})
		}, this.liveSubscription.unsubscribe.catch(() => void 0)));
	}
	dropLive() {
		this.liveSubscription?.unsubscribe.then((e) => e()).catch(() => void 0), this.liveSubscription = void 0;
	}
	renderToast(e) {
		let t = this.toastState;
		return t?.kind === e ? b`<div
      class="toast ${e}"
      @mouseenter=${() => window.clearTimeout(this.toastTimer)}
      @mouseleave=${() => this.dismissLater(e)}
    >
      ${O(e === "error" ? "alert" : "checkCircle")}<span>${t.message}</span>
    </div>` : C;
	}
	dismissLater(e) {
		window.clearTimeout(this.toastTimer), this.toastTimer = window.setTimeout(() => this.toastState = null, e === "error" ? 1e4 : 5e3);
	}
	answer(e) {
		this.dialog?.resolve(e), this.dialog = null;
	}
	get context() {
		return {
			api: this.api,
			admin: this.hass?.user?.is_admin ?? !1,
			wide: this.wide,
			navigate: this.navigate,
			toast: this.toast,
			confirm: this.confirm
		};
	}
	get staleBundle() {
		let e = this.panel?.config?.version;
		if (!e) return !1;
		let t = new URL(import.meta.url).searchParams.get("v");
		return !!t && t !== e;
	}
	screenTitle(e, t) {
		return {
			settings: P("settings.title"),
			about: P("about.title"),
			usage: P("usage.title"),
			diagnostics: P("diagnostics.title")
		}[t] ?? e.name;
	}
	back(e, t) {
		t === "about" || t === "diagnostics" ? this.navigate(`/d/${e.id}/settings`) : t === "settings" || t === "usage" ? this.navigate(`/d/${e.id}`) : this.navigate("/");
	}
	renderDeviceBody(e, t) {
		let n = this.context, r = this.live[e.id];
		switch (t) {
			case "settings": return b`<kkp-settings .hass=${this.hass} .device=${e} .live=${r} .ctx=${n}></kkp-settings>`;
			case "about": return b`<kkp-about .hass=${this.hass} .device=${e} .live=${r} .ctx=${n}></kkp-about>`;
			case "usage": return b`<kkp-usage .hass=${this.hass} .device=${e} .live=${r} .ctx=${n}></kkp-usage>`;
			case "diagnostics": return b`<kkp-diagnostics .hass=${this.hass} .device=${e} .live=${r} .ctx=${n}></kkp-diagnostics>`;
		}
		let i = t;
		return e.kind === "valve" ? b`<kkp-shower .hass=${this.hass} .device=${e} .live=${r} .ctx=${n} .tab=${i} ?wide=${this.wide}></kkp-shower>` : e.kind === "controller" ? b`<kkp-controller .hass=${this.hass} .device=${e} .live=${r} .ctx=${n} .tab=${i} ?wide=${this.wide}></kkp-controller>` : e.kind === "faucet" ? b`<kkp-faucet .hass=${this.hass} .device=${e} .live=${r} .ctx=${n} ?wide=${this.wide}></kkp-faucet>` : e.kind === "smart_fan" ? b`<kkp-smart-fan .hass=${this.hass} .device=${e} .live=${r} .ctx=${n}></kkp-smart-fan>` : b`<kkp-generic .hass=${this.hass} .device=${e} .live=${r} .ctx=${n}></kkp-generic>`;
	}
	renderDevice(e, t) {
		let n = this.data?.devices.find((t) => t.id === e);
		if (!n) return b`<div class="page">
        <kkp-header back .heading=${P("app.not_found")} @kkp-back=${() => this.navigate("/")}></kkp-header>
      </div>`;
		let r = t === "controls" || t === "favorites" || t === "experiences", i = r && !this.wide && (n.kind === "valve" || n.kind === "controller"), a = this.context.admin, o = r && (a || !!n.capabilities.usage);
		return b`<div class="device-screen"><div class="page">
        <div class="device-head">
          <kkp-header
            .heading=${this.screenTitle(n, t)}
            back
            ?more=${o}
            .moreLabel=${P(a ? "settings.title" : "usage.title")}
            @kkp-back=${() => this.back(n, t)}
            @kkp-more=${() => this.navigate(`/d/${n.id}/${a ? "settings" : "usage"}`)}
          ></kkp-header>
        </div>
        <div class="device-body">${this.renderDeviceBody(n, t)}</div>
      </div>
      ${i ? b`<kkp-capsule
            .items=${[
			{
				key: "controls",
				label: P("nav.controls"),
				icon: "controls"
			},
			{
				key: "favorites",
				label: P("nav.favorites"),
				icon: "favorite"
			},
			{
				key: "experiences",
				label: P("nav.experiences"),
				icon: "experiences"
			}
		]}
            .active=${t}
            @kkp-select=${(e) => this.navigate(e.detail === "controls" ? `/d/${n.id}` : `/d/${n.id}/${e.detail}`)}
          ></kkp-capsule>` : C}</div>`;
	}
	render() {
		if (!this.hass) return C;
		let e = Sn(this.route?.path ?? ""), t = this.data ? e.deviceId ? this.renderDevice(e.deviceId, e.screen ?? "controls") : b`<div class="page">
            <kkp-home
              .hass=${this.hass}
              .data=${this.data}
              .ctx=${this.context}
              .tab=${e.home ?? "devices"}
              ?narrow=${this.narrow}
              ?wide=${this.wide}
            ></kkp-home>
          </div>` : b`<div class="loading"><div class="spinner"></div>${P("app.loading")}</div>`;
		return b`
      ${this.staleBundle ? b`<div class="stale">${P("app.stale_bundle")}<button @click=${() => location.reload()}>${P("app.refresh")}</button></div>` : C}
      <div
        @kkp-menu=${() => this.dispatchEvent(new CustomEvent("hass-toggle-menu", {
			bubbles: !0,
			composed: !0
		}))}
      >
        ${t}
      </div>
      <!-- Live regions that stay put, so a screen reader announces each message put in
           them: errors at once, everything else when it can. -->
      <div role="status">${this.renderToast("info")}</div>
      <div role="alert">${this.renderToast("error")}</div>
      <div class="visually-hidden" aria-live="polite">${this.announcement}</div>
      <kkp-dialog
        .open=${!!this.dialog}
        .heading=${this.dialog?.title ?? ""}
        .body=${this.dialog?.body ?? ""}
        .confirmLabel=${this.dialog?.confirmLabel ?? P("dialog.confirm")}
        ?destructive=${this.dialog?.destructive ?? !1}
        @kkp-answer=${(e) => this.answer(e.detail)}
      ></kkp-dialog>
    `;
	}
};
j("kohler-konnect-panel", Cn);
export { Cn as KohlerKonnectPanel, Sn as parseRoute };
