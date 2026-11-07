const schemaDialog = document.getElementById('schemaDiagram');
const schemaViewport = document.getElementById('schemaDiagramViewport');
const schemaSvg = document.getElementById('schemaDiagramSvg');
const schemaTablePicker = document.getElementById('schemaDiagramTable');
const schemaNeighbors = document.getElementById('schemaDiagramNeighbors');
const schemaDetails = document.getElementById('schemaDiagramDetails');
const schemaStatus = document.getElementById('schemaDiagramStatus');
const schemaRefresh = document.getElementById('schemaDiagramRefresh');
const schemaZoomReset = document.getElementById('schemaZoomReset');
let diagramTables = null;
let diagramSource = '';
let diagramError = '';
let diagramCards = new Map();
let diagramLinks = [];
let diagramZoom = 1;
let diagramSize = [0, 0];

function svgElement(tag, attributes, label) {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    if (label !== undefined) element.textContent = label;
    return element;
}

function updateSchemaDiagram(tables, source, error = '') {
    diagramTables = tables;
    diagramSource = source;
    diagramError = error;
    if (schemaDialog.open) renderSchemaDiagram();
}

function resetSchemaDiagram() {
    schemaDialog.close();
    updateSchemaDiagram(null, '');
    schemaSvg.replaceChildren();
    schemaDetails.replaceChildren();
    schemaTablePicker.replaceChildren();
    schemaNeighbors.checked = false;
    diagramCards.clear();
    diagramLinks = [];
    diagramZoom = 1;
}

function resizeSchemaDiagram(zoom) {
    const previousZoom = diagramZoom;
    diagramZoom = Math.min(1.6, Math.max(0.5, Math.round(zoom * 100) / 100));
    const ratio = diagramZoom / previousZoom;
    const centerX = schemaViewport.scrollLeft + schemaViewport.clientWidth / 2;
    const centerY = schemaViewport.scrollTop + schemaViewport.clientHeight / 2;
    schemaZoomReset.textContent = `${Math.round(diagramZoom * 100)}%`;
    schemaSvg.setAttribute('width', diagramSize[0] * diagramZoom);
    schemaSvg.setAttribute('height', diagramSize[1] * diagramZoom);
    if (ratio !== 1) {
        schemaViewport.scrollTo(centerX * ratio - schemaViewport.clientWidth / 2,
            centerY * ratio - schemaViewport.clientHeight / 2);
    }
}

function selectSchemaTable(table, center = false) {
    const selected = diagramCards.get(table);
    schemaTablePicker.value = selected?.option ?? '';
    const neighbors = new Set([table]);
    const related = diagramLinks.filter(link => link.source === table || link.target === table);
    for (const link of related) {
        neighbors.add(link.source);
        neighbors.add(link.target);
    }
    for (const [name, card] of diagramCards) {
        card.group.classList.toggle('selected', name === table);
        card.group.classList.toggle('muted', Boolean(selected) && !neighbors.has(name));
        card.group.setAttribute('aria-pressed', String(name === table));
    }
    for (const link of diagramLinks) {
        const highlighted = related.includes(link);
        link.path.classList.toggle('highlighted', highlighted);
        link.path.classList.toggle('muted', Boolean(selected) && !highlighted);
        link.path.setAttribute('marker-end', highlighted ? 'url(#schemaArrowActive)' : 'url(#schemaArrow)');
        if (highlighted) link.path.parentNode.append(link.path);
    }

    schemaDetails.replaceChildren();
    if (!diagramCards.size) return;
    if (!selected || !related.length) {
        schemaDetails.textContent = selected
            ? `${table}: связи с другими таблицами схемы не найдены.`
            : 'Выбери таблицу, чтобы подсветить её связи. PK — первичный ключ, FK — внешний ключ.';
    } else {
        const list = document.createElement('ul');
        for (const link of related) {
            const item = document.createElement('li');
            item.textContent = link.label;
            list.append(item);
        }
        schemaDetails.append(list);
    }
    if (selected && center) {
        schemaViewport.scrollTo(
            (selected.x + selected.width / 2) * diagramZoom - schemaViewport.clientWidth / 2,
            (selected.y + selected.height / 2) * diagramZoom - schemaViewport.clientHeight / 2,
        );
    }
}

function chooseSchemaTable(table) {
    if (schemaNeighbors.checked) renderSchemaDiagram(table);
    else selectSchemaTable(table, true);
}

function renderSchemaDiagram(selected = schemaTablePicker.selectedOptions[0]?.dataset.table) {
    const allEntries = Object.entries(diagramTables ?? {});
    const selectedInfo = allEntries.find(([name]) => name === selected)?.[1];
    const neighbors = new Set([selected, ...((selectedInfo?.foreign_keys ?? []).map(fk => fk.table))]);
    const filtered = schemaNeighbors.checked && selectedInfo;
    const entries = filtered ? allEntries.filter(([name, info]) =>
        neighbors.has(name) || info.foreign_keys.some(fk => fk.table === selected)) : allEntries;
    schemaSvg.replaceChildren();
    schemaTablePicker.replaceChildren(new Option('Все таблицы', ''));
    const tableOptions = new Map(allEntries.map(([name], index) => {
        const option = new Option(name, String(index));
        option.dataset.table = name;
        schemaTablePicker.add(option);
        return [name, option.value];
    }));
    schemaTablePicker.disabled = !entries.length;
    document.getElementById('schemaDiagramSource').textContent = diagramSource;
    diagramCards = new Map();
    diagramLinks = [];
    schemaViewport.hidden = !entries.length;
    schemaStatus.textContent = diagramError || (diagramTables ? 'В схеме нет таблиц.' : 'Загрузка схемы…');
    if (!entries.length) {
        schemaDetails.replaceChildren();
        return;
    }

    const width = 310;
    const header = 37;
    const lineHeight = 21;
    const gap = 80;
    const columns = Math.min(entries.length, 3);
    let rowTop = 60;
    let rowHeight = 0;
    const cardLayer = svgElement('g', {});
    const edgeLayer = svgElement('g', {'aria-hidden': 'true'});
    const definitions = svgElement('defs', {});
    for (const [id, className] of [['schemaArrow', 'schema-arrow'], ['schemaArrowActive', 'schema-arrow active']]) {
        const marker = svgElement('marker', {id, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto'});
        marker.append(svgElement('path', {d: 'M 0 0 L 10 5 L 0 10 Z', class: className}));
        definitions.append(marker);
    }
    schemaSvg.append(definitions, edgeLayer, cardLayer);

    entries.forEach(([name, info], index) => {
        const x = 48 + (index % columns) * (width + gap);
        const y = rowTop;
        const height = header + info.columns.length * lineHeight + 10;
        const group = svgElement('g', {class: 'schema-card', transform: `translate(${x} ${y})`,
            role: 'button', tabindex: 0, 'aria-label': `Таблица ${name}`});
        group.append(
            svgElement('rect', {class: 'schema-card-box', width, height, rx: 9}),
            svgElement('path', {class: 'schema-card-divider', d: `M 0 ${header} H ${width}`}),
            schemaSvgLabel(name, 13, 24, 'schema-card-title', 34),
        );
        const foreignColumns = new Set(info.foreign_keys.flatMap(fk => fk.columns));
        const fieldY = new Map();
        info.columns.forEach((column, i) => {
            const baseline = header + 15 + i * lineHeight;
            fieldY.set(column.name, y + baseline - 4);
            const key = [column.pk ? 'PK' : '', foreignColumns.has(column.name) ? 'FK' : ''].filter(Boolean).join('/');
            group.append(
                schemaSvgLabel(column.name, 13, baseline, 'schema-card-field', 23),
                schemaSvgLabel(column.type || '—', 190, baseline, 'schema-card-type', 10),
                svgElement('text', {x: 299, y: baseline, 'text-anchor': 'end', class: 'schema-card-key'}, key),
            );
        });
        group.addEventListener('click', () => chooseSchemaTable(name));
        group.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                chooseSchemaTable(name);
            }
        });
        diagramCards.set(name, {x, y, width, height, fieldY, group, option: tableOptions.get(name)});
        cardLayer.append(group);
        rowHeight = Math.max(rowHeight, height);
        if ((index + 1) % columns === 0 || index + 1 === entries.length) {
            rowTop += rowHeight + gap;
            rowHeight = 0;
        }
    });

    for (const [name, info] of entries) {
        const source = diagramCards.get(name);
        for (const fk of info.foreign_keys) {
            const target = diagramCards.get(fk.table);
            if (!target || (filtered && name !== selected && fk.table !== selected)) continue;
            const offset = (diagramLinks.length % 6) * 6;
            const sy = source.fieldY.get(fk.columns[0]) ?? source.y + 18;
            const targetFieldY = target.fieldY.get(fk.referred_columns[0]) ?? target.y + 18;
            const ty = source === target && sy === targetFieldY ? target.y + 18 : targetFieldY;
            const sx = source.x + width;
            const tx = target.x + width;
            const fromLane = sx + 14 + offset;
            const toLane = tx + 14 + offset;
            const fromTop = source.y - 10 - offset;
            const toTop = target.y - 10 - offset;
            let route = `M ${sx} ${sy} H ${fromLane}`;
            if (source === target) route += ` V ${ty}`;
            else if (source.y === target.y) route += ` V ${fromTop} H ${toLane} V ${ty}`;
            else route += ` V ${fromTop} H ${9 + offset} V ${toTop} H ${toLane} V ${ty}`;
            const path = svgElement('path', {d: `${route} H ${tx + 2}`, class: 'schema-edge', 'marker-end': 'url(#schemaArrow)'});
            const label = `${name} (${fk.columns.join(', ')}) → ${fk.table} (${fk.referred_columns.join(', ')})`;
            path.append(svgElement('title', {}, label));
            edgeLayer.append(path);
            diagramLinks.push({source: name, target: fk.table, path, label});
        }
    }
    diagramSize = [48 + columns * (width + gap), rowTop - gap + 24];
    schemaSvg.setAttribute('viewBox', `0 0 ${diagramSize.join(' ')}`);
    const tableCount = filtered ? `${entries.length} из ${allEntries.length}` : entries.length;
    schemaStatus.textContent = `Таблицы: ${tableCount} · Связи: ${diagramLinks.length}. Стрелка указывает на таблицу, на которую ссылается ключ.`;
    resizeSchemaDiagram(diagramZoom);
    selectSchemaTable(selected, true);
}

function schemaSvgLabel(text, x, y, className, limit) {
    const characters = Array.from(text);
    const label = svgElement('text', {x, y, class: className},
        characters.length > limit ? characters.slice(0, limit - 1).join('') + '…' : text);
    if (characters.length > limit) label.append(svgElement('title', {}, text));
    return label;
}

document.getElementById('schemaDiagramButton').addEventListener('click', () => {
    schemaDialog.showModal();
    renderSchemaDiagram();
});
document.getElementById('schemaDiagramClose').addEventListener('click', () => schemaDialog.close());
schemaTablePicker.addEventListener('change', () => chooseSchemaTable(schemaTablePicker.selectedOptions[0]?.dataset.table));
schemaNeighbors.addEventListener('change', () => renderSchemaDiagram());
document.getElementById('schemaZoomOut').addEventListener('click', () => resizeSchemaDiagram(diagramZoom - 0.15));
document.getElementById('schemaZoomIn').addEventListener('click', () => resizeSchemaDiagram(diagramZoom + 0.15));
schemaZoomReset.addEventListener('click', () => resizeSchemaDiagram(1));
schemaRefresh.addEventListener('click', async () => {
    schemaRefresh.disabled = true;
    schemaStatus.textContent = 'Обновляю схему…';
    try { await loadSchema(true); }
    finally { schemaRefresh.disabled = false; }
});
