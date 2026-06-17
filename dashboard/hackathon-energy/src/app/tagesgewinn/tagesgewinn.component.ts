import { Component, AfterViewInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Subscription } from 'rxjs';
import { DateSelectionService } from '../services/date-selection.service';

interface DayGain {
  date: string; // DD.MM.YYYY
  gain: number; // in €
}

@Component({
  selector: 'app-tagesgewinn',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './tagesgewinn.component.html',
  styleUrls: ['./tagesgewinn.component.css'],
})
export class Tagesgewinn implements AfterViewInit, OnDestroy {
  // parsed data: date (DD.MM.YYYY) -> sum of gains for that day
  private gainByDate: Record<string, number> = {};
  dates: string[] = [];
  selectedDateIndex = 0;

  currentDayGain: number | null = null;
  currentDayDate: string | null = null;
  isProfit: boolean = true;

  private dateSub?: Subscription;
  readonly Math = Math; // Expose Math for template

  constructor(private readonly dateService: DateSelectionService) {}

  ngAfterViewInit(): void {
    this.init().catch(err => console.error('Fehler beim Initialisieren Tagesgewinn:', err));
  }

  private async init(): Promise<void> {
    try {
      const resp = await fetch('/collected_cleaned_data.csv');
      if (!resp.ok) throw new Error('Konnte CSV nicht laden');
      const txt = await resp.text();
      this.parseCsv(txt);

      // Merge available dates from other components and get current date from service
      const currentDates = (this.dateService as any)['datesSubject'].getValue() || [];
      const mergedDates = Array.from(new Set([...currentDates, ...this.dates])).sort((a, b) => {
        const pa = a.split('.').map(Number);
        const pb = b.split('.').map(Number);
        const da = new Date(pa[2], pa[1] - 1, pa[0]);
        const db = new Date(pb[2], pb[1] - 1, pb[0]);
        return da.getTime() - db.getTime();
      });
      if (mergedDates.length > 0) {
        this.dates = mergedDates;
      }

      // Get current selected date from service
      const serviceDate = (this.dateService as any)['dateIsoSubject'].getValue();
      if (serviceDate) {
        const parts = serviceDate.split('-');
        if (parts.length === 3) {
          const dateStr = `${parts[2].padStart(2, '0')}.${parts[1].padStart(2, '0')}.${parts[0]}`;
          const idx = this.dates.indexOf(dateStr);
          if (idx !== -1) {
            this.selectedDateIndex = idx;
          }
        }
      }

      // Update display with initial data
      this.updateDisplay();

      // Subscribe for date changes
      this.dateSub = this.dateService.date$.subscribe(iso => {
        if (!iso) return;
        const parts = iso.split('-');
        if (parts.length !== 3) return;
        const dateStr = `${parts[2].padStart(2, '0')}.${parts[1].padStart(2, '0')}.${parts[0]}`;
        const idx = this.dates.indexOf(dateStr);
        if (idx !== -1 && idx !== this.selectedDateIndex) {
          this.selectedDateIndex = idx;
          this.updateDisplay();
        }
      });
    } catch (err) {
      console.error(err);
    }
  }

  private parseCsv(content: string) {
    // Parse CSV with multiline support
    const rows = this.parseCSVRows(content);
    if (rows.length === 0) return;

    const header = rows[0].map(h => h.toLowerCase().replaceAll('"', ''));
    const dateIdx = header.findIndex(h => h.includes('datum'));
    const gainIdx = header.findIndex(h => h.includes('gewinn'));

    if (dateIdx === -1 || gainIdx === -1) {
      return;
    }

    let validRows = 0;
    for (let i = 1; i < rows.length; i++) {
      const cols = rows[i];
      if (cols.length <= Math.max(dateIdx, gainIdx)) continue;

      let dateStr = cols[dateIdx];
      const dateParts = dateStr.split('.');
      if (dateParts.length !== 3) continue;
      dateStr = `${dateParts[0].padStart(2, '0')}.${dateParts[1].padStart(2, '0')}.${dateParts[2]}`;

      let gainStr = cols[gainIdx] || '';
      gainStr = gainStr.replaceAll('.', '').replace(',', '.');
      const gain = Number.parseFloat(gainStr);

      if (Number.isNaN(gain)) continue;

      validRows++;
      if (!this.gainByDate[dateStr]) {
        this.gainByDate[dateStr] = 0;
      }
      this.gainByDate[dateStr] += gain / 100;
    }

    this.dates = Object.keys(this.gainByDate).sort((a, b) => {
      const pa = a.split('.').map(Number);
      const pb = b.split('.').map(Number);
      const da = new Date(pa[2], pa[1] - 1, pa[0]);
      const db = new Date(pb[2], pb[1] - 1, pb[0]);
      return da.getTime() - db.getTime();
    });
    this.selectedDateIndex = Math.max(0, this.dates.length - 1);
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
          if (currentRow.some(f => f.length > 0)) {
            rows.push(currentRow);
          }
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
      if (currentRow.some(f => f.length > 0)) {
        rows.push(currentRow);
      }
    }

    return rows;
  }

  private updateDisplay() {
    const date = this.dates[this.selectedDateIndex];
    if (!date) return;

    this.currentDayDate = date;
    this.currentDayGain = this.gainByDate[date] || 0;
    this.isProfit = this.currentDayGain >= 0;
  }

  ngOnDestroy(): void {
    if (this.dateSub) this.dateSub.unsubscribe();
  }
}


