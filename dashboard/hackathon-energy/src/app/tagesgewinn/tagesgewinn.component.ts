import { Component, AfterViewInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Subscription } from 'rxjs';
import { DateSelectionService } from '../services/date-selection.service';
import { CsvDataService } from '../services/csv-data.service';

@Component({
  selector: 'app-tagesgewinn',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './tagesgewinn.component.html',
  styleUrls: ['./tagesgewinn.component.css'],
})
export class Tagesgewinn implements AfterViewInit, OnDestroy {
  dates: string[] = [];
  selectedDateIndex = 0;

  currentDayGain: number | null = null;
  currentDayDate: string | null = null;
  isProfit: boolean = true;

  // Predicted gain display
  predictedGain: number | null = null;

  private dateSub?: Subscription;
  readonly Math = Math; // Expose Math for template

  constructor(private readonly dateService: DateSelectionService, private readonly csvService: CsvDataService) {}

  ngAfterViewInit(): void {
    this.init().catch(err => console.error('Fehler beim Initialisieren Tagesgewinn:', err));
  }

  private async init(): Promise<void> {
    try {
      await this.csvService.ensureLoaded();
      // get dates and daily summary from service
      this.dates = this.csvService.getAvailableDates();
      this.selectedDateIndex = Math.max(0, this.dates.length - 1);

      // merge with any dates from DateSelectionService
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
         } else if (idx === -1) {
           // Date not available, clear display
           this.currentDayGain = null;
           this.currentDayDate = null;
         }
       });
    } catch (err) {
      console.error(err);
    }
  }

  // CSV parsing delegated to CsvDataService

  private updateDisplay() {
    const date = this.dates[this.selectedDateIndex];
    if (!date) return;

    this.currentDayDate = date;
    // Get daily gain using the new method
    const dailyGain = this.csvService.getDailyGain(date);
    this.currentDayGain = dailyGain === 0 ? null : dailyGain;
    this.isProfit = (this.currentDayGain ?? 0) >= 0;

    // Calculate predicted gain using the new method
    this.predictedGain = null;
    const predictedGain = this.csvService.getPredictedDailyGain(date);
    if (predictedGain !== 0) {
      this.predictedGain = predictedGain;
    }
  }

  ngOnDestroy(): void {
    if (this.dateSub) this.dateSub.unsubscribe();
  }
}


