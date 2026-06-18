import { Injectable } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class CsvDataService {
  private loaded = false;
  private header: string[] = [];
  private rows: string[][] = [];
  private rowsByDate: Record<string, Array<Record<string, string>>> = {};
  private dates: string[] = [];
  private predictionByDate: Record<string, Array<Record<string, string>>> = {};
  private simulationByDate: Record<string, Array<Record<string, string>>> = {};

  // Public getter for dates (DD.MM.YYYY)
  public getAvailableDates(): string[] {
    return this.dates.slice();
  }

  // Ensure CSV loaded and parsed
  public async ensureLoaded(): Promise<void> {
    if (this.loaded) return;
    const resp = await fetch('/collected_cleaned_data.csv');
    if (!resp.ok) throw new Error('Konnte CSV nicht laden');
    const txt = await resp.text();
    this.parseCSVText(txt);
    await this.loadPredictionData();
    await this.loadSimulationData();
    this.loaded = true;
  }

  // Return raw rows (array of objects keyed by normalized header)
  public getRowsForDate(date: string): Array<Record<string, string>> {
    return this.rowsByDate[date] ? this.rowsByDate[date].slice() : [];
  }

  // Spot prices: returns { times, prices } with prices in ct/kWh
  public getSpotDataByDate(date: string): { times: string[]; prices: number[] } {
    const rows = this.getRowsForDate(date);
    const times: string[] = [];
    const prices: number[] = [];
    if (rows.length === 0) return { times, prices };

    const headerKeys = Object.keys(rows[0]);
    const timeKey = headerKeys.find(h => h.includes('uhrzeit')) ?? headerKeys.find(h => h.includes('time'));
    const priceKey = headerKeys.find(h => h.includes('spotmarktpreis')) ?? headerKeys.find(h => h.includes('spotpreis')) ?? headerKeys.find(h => h.includes('preis'));

    for (const r of rows) {
      const time = (timeKey && r[timeKey]) || '';
      const price = priceKey ? this.parseNumber(r[priceKey]) : null;
      if (typeof price === 'number') {
        times.push(time || '');
        prices.push(price);
      }
    }
    return { times, prices };
  }

  // Ausgaben: returns DayData used by Ausgaben component
  public getAusgabenDataByDate(date: string): { times: string[]; taxesAndCharges: number[]; workPrice: number[]; spotPrice: number[] } {
    const rows = this.getRowsForDate(date);
    const times: string[] = [];
    const taxesAndCharges: number[] = [];
    const workPrice: number[] = [];
    const spotPrice: number[] = [];
    if (rows.length === 0) return { times, taxesAndCharges, workPrice, spotPrice };

    const headerKeys = Object.keys(rows[0]);
    const timeKey = headerKeys.find(h => h.includes('uhrzeit')) ?? headerKeys.find(h => h.includes('time'));
    const spotKey = headerKeys.find(h => h.includes('spotmarktpreis')) ?? headerKeys.find(h => h.includes('spotpreis')) ?? headerKeys.find(h => h.includes('spot'));
    const workKey = headerKeys.find(h => h.includes('arbeitspreis')) ?? headerKeys.find(h => h.includes('arbeit'));
    const taxKey = headerKeys.find(h => h.includes('steuern')) ?? headerKeys.find(h => h.includes('abgaben')) ?? headerKeys.find(h => h.includes('steuer'));

    for (const r of rows) {
      const time = (timeKey && r[timeKey]) || '';
      const spot = spotKey ? this.parseNumber(r[spotKey]) : null;
      const work = workKey ? this.parseNumber(r[workKey]) : null;
      const tax = taxKey ? this.parseNumber(r[taxKey]) : null;
      if (typeof spot === 'number' && typeof work === 'number' && typeof tax === 'number') {
        times.push(time);
        taxesAndCharges.push(tax + work);
        workPrice.push(0);
        spotPrice.push(spot);
      }
    }
    return { times, taxesAndCharges, workPrice, spotPrice };
  }

  // Gewinn per timestamp (values converted to €)
  // Berechnung: ProfilwertKwh * (Endkundenpreis - Steuern - Spotmarktpreis - Arbeitspreis)
  public getGewinnDataByDate(date: string): { times: string[]; profit: number[] } {
    const rows = this.getRowsForDate(date);
    const times: string[] = [];
    const profit: number[] = [];

    const headerKeys = Object.keys(rows[0] || {});
    const timeKey = headerKeys.find(h => h.includes('uhrzeit')) ?? headerKeys.find(h => h.includes('time'));
    const loadKey = headerKeys.find(h => h.includes('profilwert') && h.includes('kwh')) ?? headerKeys.find(h => h.includes('profilwert'));
    const endpriceKey = headerKeys.find(h => h.includes('endkundenpreis')) ?? headerKeys.find(h => h.includes('kundenpreis'));
    const taxKey = headerKeys.find(h => h.includes('steuern')) ?? headerKeys.find(h => h.includes('abgaben'));
    const spotKey = headerKeys.find(h => h.includes('spotmarktpreis')) ?? headerKeys.find(h => h.includes('spotpreis'));
    const workKey = headerKeys.find(h => h.includes('arbeitspreis')) ?? headerKeys.find(h => h.includes('arbeit'));

    for (const r of rows) {
      const time = (timeKey && r[timeKey]) || '';
      const load = loadKey ? this.parseNumber(r[loadKey]) : null;
      const endPrice = endpriceKey ? this.parseNumber(r[endpriceKey]) : null;
      const tax = taxKey ? this.parseNumber(r[taxKey]) : null;
      const spot = spotKey ? this.parseNumber(r[spotKey]) : null;
      const work = workKey ? this.parseNumber(r[workKey]) : null;

      if (typeof load === 'number' && typeof endPrice === 'number' && typeof tax === 'number' &&
          typeof spot === 'number' && typeof work === 'number') {
        const gain = load * (endPrice - tax - spot - work);
        times.push(time);
        profit.push(gain / 100);
      }
    }
    return { times, profit };
  }

  // Auslastung: returns times, loads (kWh) and optional prices (ct/kWh) aligned with times
  public getAuslastungDataByDate(date: string): { times: string[]; loads: number[]; prices?: Array<number | null> } {
    const rows = this.getRowsForDate(date);
    const times: string[] = [];
    const loads: number[] = [];
    const prices: Array<number | null> = [];
    if (rows.length === 0) return { times, loads };

    const headerKeys = Object.keys(rows[0]);
    const timeKey = headerKeys.find(h => h.includes('uhrzeit')) ?? headerKeys.find(h => h.includes('time'));
    const loadKey = headerKeys.find(h => h.includes('profilwert') && h.includes('kwh')) ?? headerKeys.find(h => h.includes('profilwert'));
    const priceKey = headerKeys.find(h => h.includes('endkundenpreis')) ?? headerKeys.find(h => h.includes('kundenpreis')) ?? headerKeys.find(h => h.includes('preis'));

    for (const r of rows) {
      const time = (timeKey && r[timeKey]) || '';
      const load = loadKey ? this.parseNumber(r[loadKey]) : null;
      const price = priceKey ? this.parseNumber(r[priceKey]) : null;
      if (load === null || typeof load !== 'number') continue;
      times.push(time);
      loads.push(load);
      prices.push(price === null ? null : price);
    }
    return { times, loads, prices };
  }

  // Tagesgewinn summary: date -> sum in €
  // Berechnung: ProfilwertKwh * (Endkundenpreis - Steuern - Spotmarktpreis - Arbeitspreis)
  public getDailyGainSummary(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const d of this.dates) {
      const rows = this.getRowsForDate(d);
      let sum = 0;

      // Get header keys for field lookup
      const headerKeys = Object.keys(rows[0] || {});
      const loadKey = headerKeys.find(h => h.includes('profilwert') && h.includes('kwh')) ?? headerKeys.find(h => h.includes('profilwert'));
      const endpriceKey = headerKeys.find(h => h.includes('endkundenpreis')) ?? headerKeys.find(h => h.includes('kundenpreis'));
      const taxKey = headerKeys.find(h => h.includes('steuern')) ?? headerKeys.find(h => h.includes('abgaben'));
      const spotKey = headerKeys.find(h => h.includes('spotmarktpreis')) ?? headerKeys.find(h => h.includes('spotpreis'));
      const workKey = headerKeys.find(h => h.includes('arbeitspreis')) ?? headerKeys.find(h => h.includes('arbeit'));

      for (const r of rows) {
        const load = loadKey ? this.parseNumber(r[loadKey]) : null;
        const endPrice = endpriceKey ? this.parseNumber(r[endpriceKey]) : null;
        const tax = taxKey ? this.parseNumber(r[taxKey]) : null;
        const spot = spotKey ? this.parseNumber(r[spotKey]) : null;
        const work = workKey ? this.parseNumber(r[workKey]) : null;

        if (typeof load === 'number' && typeof endPrice === 'number' && typeof tax === 'number' &&
            typeof spot === 'number' && typeof work === 'number') {
          const gain = load * (endPrice - tax - spot - work);
          sum += gain;
        }
      }
      out[d] = sum / 100;
    }
    return out;
  }

  // Prediction data: get price_ct values and times for a date (format: DD.MM.YYYY)
  public getPredictionDataByDate(date: string): { times: string[]; prices: Array<number | null>; forecasts?: Array<number | null> } {
    // Convert DD.MM.YYYY to YYYY-MM-DD if needed
    let isoDate = date;
    if (date.includes('.')) {
      const parts = date.split('.');
      if (parts.length === 3) {
        isoDate = `${parts[2]}-${parts[1]}-${parts[0]}`;
      }
    }

    const rows = this.predictionByDate[isoDate] || [];
    const times: string[] = [];
    const prices: Array<number | null> = [];
    const forecasts: Array<number | null> = [];

    if (rows.length === 0) {
      console.log(`No prediction data for date: ${isoDate}`);
      return { times, prices, forecasts };
    }

    const headerKeys = Object.keys(rows[0]);

    const timeKey = headerKeys.find(h => h.toLowerCase().includes('hourstamp')) ??
                    headerKeys.find(h => h.toLowerCase().includes('uhrzeit')) ??
                    headerKeys.find(h => h.toLowerCase().includes('time'));
    const priceKey = headerKeys.find(h => h.toLowerCase().includes('price_ct')) ??
                     headerKeys.find(h => h.toLowerCase().includes('price'));
    const forecastKey = headerKeys.find(h => h.toLowerCase().includes('forecast_kwh')) ??
                        headerKeys.find(h => h.toLowerCase().includes('forecast'));


    for (const r of rows) {
      let time = timeKey ? r[timeKey] : '';
      // Extract HH:MM:SS from hourstamp if available
      if (time && time.includes(' ')) {
        time = time.split(' ')[1]; // Get time part after space
      }
      const price = priceKey ? this.parseNumber(r[priceKey]) : null;
      const forecast = forecastKey ? this.parseNumber(r[forecastKey]) : null;
      times.push(time || '');
      prices.push(price === null ? null : price);
      forecasts.push(forecast === null ? null : forecast);
    }
    return { times, prices, forecasts };
  }

  // Legacy method for backward compatibility
  public getPredictionPricesByDate(date: string): Array<number | null> {
    const data = this.getPredictionDataByDate(date);
    return data.prices;
  }

  // Check if prediction data exists for a date
  public hasPredictionData(date: string): boolean {
    // Convert DD.MM.YYYY to YYYY-MM-DD if needed
    let isoDate = date;
    if (date.includes('.')) {
      const parts = date.split('.');
      if (parts.length === 3) {
        isoDate = `${parts[2]}-${parts[1]}-${parts[0]}`;
      }
    }
    const rows = this.predictionByDate[isoDate] || [];
    return rows.length > 0;
  }

  // Load prediction CSV files for all available dates
  private async loadPredictionData(): Promise<void> {
    // List common date formats to try loading
    const datesToTry: string[] = [];

    // Add dates for the past 60 days and next 30 days
    const today = new Date();
    for (let i = -60; i < 30; i++) {
      const date = new Date(today);
      date.setDate(date.getDate() + i);
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const day = String(date.getDate()).padStart(2, '0');
      datesToTry.push(`${year}-${month}-${day}`);
    }


    // Try loading each prediction file
    for (const dateStr of datesToTry) {
      try {
        const resp = await fetch(`/prediction/plan_${dateStr}.csv`);
        if (!resp.ok) continue;
        const txt = await resp.text();
        this.parsePredictionCSV(txt, dateStr);
      } catch (err) {
        // Silently continue if file not found
      }
    }
  }

  // Parse prediction CSV (uses comma as delimiter, not semicolon)
  private parsePredictionCSV(content: string, dateStr: string): void {
    const rows = this.parseCSVRowsWithDelimiter(content, ',');
    if (rows.length === 0) return;

    const header = rows[0].map(h => h.toLowerCase().replaceAll('"', '').trim());
    this.predictionByDate[dateStr] = [];

    for (let i = 1; i < rows.length; i++) {
      const cols = rows[i];
      const obj: Record<string, string> = {};
      for (let c = 0; c < header.length; c++) {
        obj[header[c]] = cols[c] ?? '';
      }
      this.predictionByDate[dateStr].push(obj);
    }
  }

  // Parse CSV rows with a specified delimiter
  private parseCSVRowsWithDelimiter(content: string, delimiter: string): string[][] {
    const rows: string[][] = [];
    let currentRow: string[] = [];
    let currentField = '';
    let inQuotes = false;

    for (let i = 0; i < content.length; i++) {
      const char = content[i];
      const nextChar = content[i + 1];

      if (char === '"') {
        if (inQuotes && nextChar === '"') {
          currentField += '"';
          i++;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (char === delimiter && !inQuotes) {
        currentRow.push(currentField.trim());
        currentField = '';
      } else if ((char === '\n' || char === '\r') && !inQuotes) {
        if (currentField || currentRow.length > 0) {
          currentRow.push(currentField.trim());
          if (currentRow.some(f => f.length > 0)) rows.push(currentRow);
          currentRow = [];
          currentField = '';
        }
        if (char === '\r' && nextChar === '\n') i++;
      } else {
        currentField += char;
      }
    }

    if (currentField || currentRow.length > 0) {
      currentRow.push(currentField.trim());
      if (currentRow.some(f => f.length > 0)) rows.push(currentRow);
    }

    return rows;
  }

  // Load simulation CSV files for all available dates
  private async loadSimulationData(): Promise<void> {
    // List common date formats to try loading
    const datesToTry: string[] = [];

    // Add dates for the past 60 days and next 30 days
    const today = new Date();
    for (let i = -60; i < 30; i++) {
      const date = new Date(today);
      date.setDate(date.getDate() + i);
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const day = String(date.getDate()).padStart(2, '0');
      datesToTry.push(`${year}-${month}-${day}`);
    }

    // Try loading each simulation file
    for (const dateStr of datesToTry) {
      try {
        const resp = await fetch(`/simulation/simulated_data_${dateStr}.csv`);
        if (!resp.ok) continue;
        const txt = await resp.text();
        this.parseSimulationCSV(txt, dateStr);
      } catch (err) {
        // Silently continue if file not found
      }
    }
  }

  // Parse simulation CSV (uses comma as delimiter, like prediction data)
  private parseSimulationCSV(content: string, dateStr: string): void {
    const rows = this.parseCSVRowsWithDelimiter(content, ',');
    if (rows.length === 0) return;

    const header = rows[0].map(h => h.toLowerCase().replaceAll('"', '').trim());
    this.simulationByDate[dateStr] = [];

    for (let i = 1; i < rows.length; i++) {
      const cols = rows[i];
      const obj: Record<string, string> = {};
      for (let c = 0; c < header.length; c++) {
        obj[header[c]] = cols[c] ?? '';
      }
      this.simulationByDate[dateStr].push(obj);
    }
  }

  // Get simulation data - similar to prediction but for future dates
  public getSimulationDataByDate(date: string): { times: string[]; loads: Array<number | null>; prices: Array<number | null> } {
    // Convert DD.MM.YYYY to YYYY-MM-DD if needed
    let isoDate = date;
    if (date.includes('.')) {
      const parts = date.split('.');
      if (parts.length === 3) {
        isoDate = `${parts[2]}-${parts[1]}-${parts[0]}`;
      }
    }

    const rows = this.simulationByDate[isoDate] || [];
    const times: string[] = [];
    const loads: Array<number | null> = [];
    const prices: Array<number | null> = [];

    if (rows.length === 0) {
      return { times, loads, prices };
    }

    const headerKeys = Object.keys(rows[0]);

    const timeKey = headerKeys.find(h => h.toLowerCase().includes('hourstamp')) ??
                    headerKeys.find(h => h.toLowerCase().includes('time'));
    const loadKey = headerKeys.find(h => h.toLowerCase().includes('actual_kwh')) ??
                    headerKeys.find(h => h.toLowerCase().includes('kwh'));
    const priceKey = headerKeys.find(h => h.toLowerCase().includes('charged_price_ct')) ??
                     headerKeys.find(h => h.toLowerCase().includes('price'));

    for (const r of rows) {
      let time = timeKey ? r[timeKey] : '';
      // Extract HH:MM:SS from hourstamp if available
      if (time && time.includes(' ')) {
        time = time.split(' ')[1]; // Get time part after space
      }
      const load = loadKey ? this.parseNumber(r[loadKey]) : null;
      const price = priceKey ? this.parseNumber(r[priceKey]) : null;
      times.push(time || '');
      loads.push(load === null ? null : load);
      prices.push(price === null ? null : price);
    }
    return { times, loads, prices };
  }

  // Get spot prices from prediction data
  public getSpotPredictionDataByDate(date: string): { times: string[]; prices: Array<number | null> } {
    // Convert DD.MM.YYYY to YYYY-MM-DD if needed
    let isoDate = date;
    if (date.includes('.')) {
      const parts = date.split('.');
      if (parts.length === 3) {
        isoDate = `${parts[2]}-${parts[1]}-${parts[0]}`;
      }
    }

    const rows = this.predictionByDate[isoDate] || [];
    const times: string[] = [];
    const prices: Array<number | null> = [];

    if (rows.length === 0) {
      return { times, prices };
    }

    const headerKeys = Object.keys(rows[0]);

    const timeKey = headerKeys.find(h => h.toLowerCase().includes('hourstamp')) ??
                    headerKeys.find(h => h.toLowerCase().includes('time'));
    const spotKey = headerKeys.find(h => h.toLowerCase().includes('spot_ct')) ??
                    headerKeys.find(h => h.toLowerCase().includes('spot'));

    for (const r of rows) {
      let time = timeKey ? r[timeKey] : '';
      // Extract HH:MM:SS from hourstamp if available
      if (time && time.includes(' ')) {
        time = time.split(' ')[1]; // Get time part after space
      }
      const price = spotKey ? this.parseNumber(r[spotKey]) : null;
      times.push(time || '');
      prices.push(price === null ? null : price);
    }
    return { times, prices };
  }

  // Get spot prices from simulation data
  public getSpotSimulationDataByDate(date: string): { times: string[]; prices: Array<number | null> } {
    // Convert DD.MM.YYYY to YYYY-MM-DD if needed
    let isoDate = date;
    if (date.includes('.')) {
      const parts = date.split('.');
      if (parts.length === 3) {
        isoDate = `${parts[2]}-${parts[1]}-${parts[0]}`;
      }
    }

    const rows = this.simulationByDate[isoDate] || [];
    const times: string[] = [];
    const prices: Array<number | null> = [];

    if (rows.length === 0) {
      return { times, prices };
    }

    const headerKeys = Object.keys(rows[0]);

    const timeKey = headerKeys.find(h => h.toLowerCase().includes('hourstamp')) ??
                    headerKeys.find(h => h.toLowerCase().includes('time'));
    const spotKey = headerKeys.find(h => h.toLowerCase().includes('spot_ct')) ??
                    headerKeys.find(h => h.toLowerCase().includes('spot'));

    for (const r of rows) {
      let time = timeKey ? r[timeKey] : '';
      // Extract HH:MM:SS from hourstamp if available
      if (time && time.includes(' ')) {
        time = time.split(' ')[1]; // Get time part after space
      }
      const price = spotKey ? this.parseNumber(r[spotKey]) : null;
      times.push(time || '');
      prices.push(price === null ? null : price);
    }
    return { times, prices };
  }

  // Get profit from prediction data
  // Berechnung: forecast_kwh * (price_ct - Steuern(6.961) - spot_ct - Arbeitspreis(7.48))
  public getProfitPredictionDataByDate(date: string): { times: string[]; profit: Array<number | null> } {
    // Convert DD.MM.YYYY to YYYY-MM-DD if needed
    let isoDate = date;
    if (date.includes('.')) {
      const parts = date.split('.');
      if (parts.length === 3) {
        isoDate = `${parts[2]}-${parts[1]}-${parts[0]}`;
      }
    }

    const rows = this.predictionByDate[isoDate] || [];
    const times: string[] = [];
    const profit: Array<number | null> = [];

    if (rows.length === 0) {
      return { times, profit };
    }

    const headerKeys = Object.keys(rows[0]);
    const TAXES = 6.961;
    const WORK_PRICE = 7.48;

    const timeKey = headerKeys.find(h => h.toLowerCase().includes('hourstamp')) ??
                    headerKeys.find(h => h.toLowerCase().includes('time'));
    const forecastKey = headerKeys.find(h => h.toLowerCase().includes('forecast_kwh')) ??
                        headerKeys.find(h => h.toLowerCase().includes('forecast'));
    const priceKey = headerKeys.find(h => h.toLowerCase().includes('price_ct')) ??
                     headerKeys.find(h => h.toLowerCase().includes('price'));
    const spotKey = headerKeys.find(h => h.toLowerCase().includes('spot_ct')) ??
                    headerKeys.find(h => h.toLowerCase().includes('spot'));

    for (const r of rows) {
      let time = timeKey ? r[timeKey] : '';
      // Extract HH:MM:SS from hourstamp if available
      if (time && time.includes(' ')) {
        time = time.split(' ')[1];
      }

      const forecast = forecastKey ? this.parseNumber(r[forecastKey]) : null;
      const price = priceKey ? this.parseNumber(r[priceKey]) : null;
      const spot = spotKey ? this.parseNumber(r[spotKey]) : null;

      let gainValue: number | null = null;
      if (forecast !== null && price !== null && spot !== null) {
        gainValue = forecast * (price - TAXES - spot - WORK_PRICE) / 100; // Convert to €
      }
      console.log(`Prediction profit for time ${time}: forecast=${forecast}, price=${price}, spot=${spot} => gain=${gainValue}`);

      times.push(time || '');
      profit.push(gainValue);
    }
    return { times, profit };
  }

  // Get profit from simulation data
  // Berechnung: actual_kwh * (charged_price_ct - Steuern(6.961) - spot_ct - Arbeitspreis(7.48))
  public getProfitSimulationDataByDate(date: string): { times: string[]; profit: Array<number | null> } {
    // Convert DD.MM.YYYY to YYYY-MM-DD if needed
    let isoDate = date;
    if (date.includes('.')) {
      const parts = date.split('.');
      if (parts.length === 3) {
        isoDate = `${parts[2]}-${parts[1]}-${parts[0]}`;
      }
    }

    const rows = this.simulationByDate[isoDate] || [];
    const times: string[] = [];
    const profit: Array<number | null> = [];

    if (rows.length === 0) {
      return { times, profit };
    }

    const headerKeys = Object.keys(rows[0]);
    const TAXES = 6.961;
    const WORK_PRICE = 7.48;

    const timeKey = headerKeys.find(h => h.toLowerCase().includes('hourstamp')) ??
                    headerKeys.find(h => h.toLowerCase().includes('time'));
    const loadKey = headerKeys.find(h => h.toLowerCase().includes('actual_kwh')) ??
                    headerKeys.find(h => h.toLowerCase().includes('kwh'));
    const priceKey = headerKeys.find(h => h.toLowerCase().includes('charged_price_ct')) ??
                     headerKeys.find(h => h.toLowerCase().includes('price'));
    const spotKey = headerKeys.find(h => h.toLowerCase().includes('spot_ct')) ??
                    headerKeys.find(h => h.toLowerCase().includes('spot'));

    for (const r of rows) {
      let time = timeKey ? r[timeKey] : '';
      // Extract HH:MM:SS from hourstamp if available
      if (time && time.includes(' ')) {
        time = time.split(' ')[1];
      }

      const load = loadKey ? this.parseNumber(r[loadKey]) : null;
      const price = priceKey ? this.parseNumber(r[priceKey]) : null;
      const spot = spotKey ? this.parseNumber(r[spotKey]) : null;

      let gainValue: number | null = null;
      if (load !== null && price !== null && spot !== null) {
        gainValue = load * (price - TAXES - spot - WORK_PRICE) / 100; // Convert to €
      }

      times.push(time || '');
      profit.push(gainValue);
    }
    return { times, profit };
  }

  // Calculate daily gain from collected data or simulation data
  // If simulation data exists, use it; otherwise use main collected data
  // Berechnung: ProfilwertKwh * (Endkundenpreis - Steuern - Spotmarktpreis - Arbeitspreis)
  public getDailyGain(date: string): number {
    // Convert DD.MM.YYYY to YYYY-MM-DD if needed
    let isoDate = date;
    if (date.includes('.')) {
      const parts = date.split('.');
      if (parts.length === 3) {
        isoDate = `${parts[2]}-${parts[1]}-${parts[0]}`;
      }
    }

    // First try to get simulation data
    const simData = this.getProfitSimulationDataByDate(date);
    if (simData.profit.length > 0) {
      const gains = simData.profit.filter((v): v is number => v !== null && v !== undefined);
      if (gains.length > 0) {
        return gains.reduce((sum, val) => sum + val, 0);
      }
    }

    // Fall back to main collected data
    const mainData = this.getGewinnDataByDate(date);
    console.log("mainData");
    console.log(mainData);
    if (mainData.profit.length > 0) {
      const gains = mainData.profit.filter((v): v is number => v !== null && v !== undefined);
      if (gains.length > 0) {
        return gains.reduce((sum, val) => sum + val, 0);
      }
    }

    return 0;
  }

  // Calculate predicted daily gain from prediction data
  // Berechnung: forecast_kwh * (price_ct - Steuern(6.961) - spot_ct - Arbeitspreis(7.48))
  public getPredictedDailyGain(date: string): number {
    const predData = this.getProfitPredictionDataByDate(date);
    console.log(predData);
    if (predData.profit.length > 0) {
      const gains = predData.profit.filter((v): v is number => v !== null && v !== undefined);
      if (gains.length > 0) {
        return gains.reduce((sum, val) => sum + val, 0);
      }
    }
    return 0;
  }

  // --- internal helpers ---
  private parseCSVText(content: string) {
    this.rows = [];
    const rows = this.parseCSVRows(content);
    if (rows.length === 0) return;
    this.header = rows[0].map(h => h.toLowerCase().replaceAll('"', '').trim());
    const headersNorm = this.header.map(h => h.replaceAll(' ', '').replaceAll("\"", ''));
    this.rowsByDate = {};

    for (let i = 1; i < rows.length; i++) {
      const cols = rows[i];
      const obj: Record<string, string> = {};
      for (let c = 0; c < this.header.length; c++) {
        obj[this.header[c]] = cols[c] ?? '';
      }

      // Determine date column (try several header names)
      const dateRaw = obj['datum'] ?? obj['date'] ?? '';
      const dateNorm = this.normalizeDate(dateRaw);
      if (!dateNorm) continue;

      // store using lowercase header keys for easier lookup
      const keyed: Record<string, string> = {};
      for (const k of Object.keys(obj)) keyed[k.toLowerCase()] = obj[k];

      if (!this.rowsByDate[dateNorm]) this.rowsByDate[dateNorm] = [];
      this.rowsByDate[dateNorm].push(keyed);
    }

    this.dates = Object.keys(this.rowsByDate).sort((a, b) => {
      const pa = a.split('.').map(Number);
      const pb = b.split('.').map(Number);
      const da = new Date(pa[2], pa[1] - 1, pa[0]);
      const db = new Date(pb[2], pb[1] - 1, pb[0]);
      return da.getTime() - db.getTime();
    });
  }

  private normalizeDate(raw: string): string | null {
    if (!raw) return null;
    const parts = raw.trim().split('.');
    if (parts.length !== 3) return null;
    return `${parts[0].padStart(2, '0')}.${parts[1].padStart(2, '0')}.${parts[2]}`;
  }

  private parseNumber(s: string | undefined | null): number | null {
    if (s === undefined || s === null) return null;
    let t = String(s).replaceAll('"', '').trim();
    if (t.length === 0) return null;

    // Detect format: if there's a comma and a dot, assume German format (1.234,56)
    // If there's only a comma, assume German format (45,5)
    // If there's only a dot, assume English format (40.78)
    const hasComma = t.includes(',');
    const hasDot = t.includes('.');

    if (hasComma && hasDot) {
      // German format: 1.234,56 -> remove dots, replace comma with dot
      t = t.replaceAll('.', '').replace(',', '.');
    } else if (hasComma && !hasDot) {
      // German format: 45,5 -> replace comma with dot
      t = t.replace(',', '.');
    }
    // else: English format, keep as is

    const v = Number.parseFloat(t);
    if (Number.isNaN(v)) return null;
    return v;
  }

  private parseCSVRows(content: string): string[][] {
    const rows: string[][] = [];
    let currentRow: string[] = [];
    let currentField = '';
    let inQuotes = false;

    for (let i = 0; i < content.length; i++) {
      const char = content[i];
      const nextChar = content[i + 1];

      if (char === '"') {
        if (inQuotes && nextChar === '"') {
          currentField += '"';
          i++;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (char === ';' && !inQuotes) {
        currentRow.push(currentField.trim());
        currentField = '';
      } else if ((char === '\n' || char === '\r') && !inQuotes) {
        if (currentField || currentRow.length > 0) {
          currentRow.push(currentField.trim());
          if (currentRow.some(f => f.length > 0)) rows.push(currentRow);
          currentRow = [];
          currentField = '';
        }
        if (char === '\r' && nextChar === '\n') i++;
      } else {
        currentField += char;
      }
    }

    if (currentField || currentRow.length > 0) {
      currentRow.push(currentField.trim());
      if (currentRow.some(f => f.length > 0)) rows.push(currentRow);
    }

    return rows;
  }
}
