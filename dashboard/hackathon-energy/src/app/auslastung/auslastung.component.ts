import { Component, ElementRef, ViewChild, AfterViewInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import * as echarts from 'echarts/core';
import { BarChart } from 'echarts/charts';
import { TitleComponent, TooltipComponent, GridComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Subscription } from 'rxjs';
import { DateSelectionService } from '../services/date-selection.service';
import { CsvDataService } from '../services/csv-data.service';

// Register required ECharts components for bar chart
echarts.use([BarChart, TitleComponent, TooltipComponent, GridComponent, LegendComponent, CanvasRenderer]);

@Component({
  selector: 'app-auslastung',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './auslastung.component.html',
  styleUrls: ['./auslastung.component.css'],
})
export class Auslastung implements AfterViewInit, OnDestroy {
  @ViewChild('chart', { static: true }) chartEl!: ElementRef<HTMLDivElement>;
  private chartInstance: echarts.ECharts | null = null;

  // parsed data: date (DD.MM.YYYY) -> { times: string[], loads: number[]; prices?: number[] }
  private dataByDate: Record<string, { times: string[]; loads: number[]; prices?: Array<number | null> }> = {};
  dates: string[] = [];
  selectedDateIndex = 0;


  // price axis defaults (ct/kWh)
  private priceDefaultYMin = -5;
  private priceDefaultYMax = 50;


  private dateSub?: Subscription;

  constructor(private readonly dateService: DateSelectionService, private readonly csvService: CsvDataService) {}

  ngAfterViewInit(): void {
    this.chartInstance = echarts.init(this.chartEl.nativeElement);
    this.init().catch(err => console.error('Fehler beim Initialisieren Auslastung:', err));
  }

  private async init(): Promise<void> {
    try {
      await this.csvService.ensureLoaded();
      this.dates = this.csvService.getAvailableDates();
      this.selectedDateIndex = Math.max(0, this.dates.length - 1);

      const initDate = this.selectedDate();
      if (initDate) this.updateChartForDate(initDate);

      // Merge available dates from both components and publish to service
      const currentDates = (this.dateService as any)['datesSubject'].getValue() || [];
      const mergedDates = Array.from(new Set([...currentDates, ...this.dates])).sort((a, b) => {
        const pa = a.split('.').map(Number);
        const pb = b.split('.').map(Number);
        const da = new Date(pa[2], pa[1] - 1, pa[0]);
        const db = new Date(pb[2], pb[1] - 1, pb[0]);
        return da.getTime() - db.getTime();
      });
      if (mergedDates.length > 0) {
        this.dateService.setAvailableDates(mergedDates);
      }

       // subscribe for date changes from the global DateSelectionService
       this.dateSub = this.dateService.date$.subscribe(iso => {
         if (!iso) return;
         const parts = iso.split('-');
         if (parts.length !== 3) return;

         // build dd.mm.yyyy string for lookup
         const dateStr = `${parts[2].padStart(2, '0')}.${parts[1].padStart(2, '0')}.${parts[0]}`;

         // Show the selected date if available OR if prediction data exists
         const idx = this.dates.indexOf(dateStr);
         const hasRealData = idx !== -1;
         const hasPrediction = this.csvService.hasPredictionData(dateStr);

         if (hasRealData && idx !== this.selectedDateIndex) {
           this.selectedDateIndex = idx;
           this.updateChartForDate(dateStr);
         } else if (hasPrediction) {
           // Show prediction data even without real data
           this.updateChartForDate(dateStr);
         } else if (!hasRealData && !hasPrediction) {
           // No data available, show empty chart
           this.updateChartForDate(dateStr);
         }
       });

      window.addEventListener('resize', this.onResize);
    } catch (err) {
      console.error(err);
    }
  }

  public selectedDate(): string | null {
    return this.dates.length > 0 ? this.dates[this.selectedDateIndex] : null;
  }

  // CSV parsing delegated to CsvDataService

   private updateChartForDate(date: string) {
     const d = this.csvService.getAuslastungDataByDate(date);
     const predictionData = this.csvService.getPredictionDataByDate(date);
     const simulationData = this.csvService.getSimulationDataByDate(date);

      // Check if we have any data (real or simulation or prediction)
      const hasRealData = d && d.times.length > 0;
      const hasSimulationData = !hasRealData && simulationData && simulationData.times.length > 0;
      const hasPredictionData = predictionData && predictionData.times.length > 0;

       if (!hasRealData && !hasSimulationData && !hasPredictionData) {
        // Show empty chart if no data available
        const emptyOption: any = {
          title: { text: `Auslastung`, textStyle: { color: '#000' } },
          tooltip: { trigger: 'axis' },
          xAxis: { type: 'category', data: [], axisLabel: { color: '#000' } },
          yAxis: [{ type: 'value', name: 'Last (kWh)', axisLabel: { color: '#000' } }],
          series: [],
          grid: { left: '10%', right: '10%', bottom: '15%' },
        };
        this.chartInstance?.setOption(emptyOption, true);
        return;
      }

      // Create full 24-hour time axis (00:00 to 23:00)
      const fullTimes = [];
      for (let h = 0; h < 24; h++) {
        fullTimes.push(`${String(h).padStart(2, '0')}:00:00`);
      }

      // Use real data, or simulation data if not available
      const dataSource = hasRealData ? d : (hasSimulationData ? { times: simulationData.times, loads: simulationData.loads, prices: simulationData.prices } : null);

      // Map data to the full 24-hour axis
     const mapDataToFullAxis = (sourceTimes: string[], sourceData: any[]): any[] => {
       const fullData = new Array(24).fill(null);

       for (let i = 0; i < sourceTimes.length; i++) {
         const time = sourceTimes[i];
         const hour = parseInt(time.split(':')[0]);
         if (hour >= 0 && hour < 24) {
           fullData[hour] = sourceData[i];
         }
       }

       return fullData;
     };

      // Map loads and prices to the full axis
      const fullLoads = (hasRealData || hasSimulationData) && dataSource ? mapDataToFullAxis(dataSource.times, dataSource.loads) : new Array(24).fill(null);
      const fullPrices = (hasRealData || hasSimulationData) && dataSource && dataSource.prices ? mapDataToFullAxis(dataSource.times, dataSource.prices) : new Array(24).fill(null);
     const fullPredictionPrices = hasPredictionData ? mapDataToFullAxis(predictionData.times, predictionData.prices) : new Array(24).fill(null);
     const fullForecasts = hasPredictionData && predictionData.forecasts ? mapDataToFullAxis(predictionData.times, predictionData.forecasts) : new Array(24).fill(null);

      const option: any = {
        title: { text: `Auslastung`, textStyle: { color: '#000' } },
        legend: {
          data: ['Last', 'Vorhersage-Auslastung', 'Kundenpreis', 'Vorhersagepreis'],
          top: '4%',
          left: 'center',
          textStyle: { color: '#000' },
        },
        tooltip: { trigger: 'axis' },
        xAxis: {
          type: 'category',
          data: fullTimes,
          boundaryGap: false,
          // axis name removed: keep tick labels but no axis title
          axisLabel: {
            interval: 2, // Show every 3rd hour
            color: '#000',
            formatter: (value: string) => {
              // Remove seconds if present (HH:MM:SS -> HH:MM)
              const parts = value.split(':');
              return parts.length >= 2 ? `${parts[0]}:${parts[1]}` : value;
            },
          },
        },
        yAxis: [{ type: 'value', name: 'Last (kWh)', axisLabel: { color: '#000' } }],
        series: [],
        grid: { left: '10%', right: '10%', bottom: '15%' },
      };

      // Add real or simulation data loads if available
      if (hasRealData || hasSimulationData) {
        option.series.push({
          name: 'Last',
          type: 'bar',
          data: fullLoads,
          itemStyle: {
            color: '#7300ff',
            opacity: 0.9 },
          yAxisIndex: 0,
        });
      }


      // Add real or simulation prices if available
      if ((hasRealData || hasSimulationData) && fullPrices && fullPrices.some(p => p !== null && p !== undefined)) {
        const numericPrices = fullPrices.filter((p): p is number => p !== null && p !== undefined);
        const numericPrediction = fullPredictionPrices.filter((p): p is number => p !== null && p !== undefined);

        // Calculate bounds including prediction data
        let dayMin = numericPrices.length ? Math.min(...numericPrices) : Number.POSITIVE_INFINITY;
        let dayMax = numericPrices.length ? Math.max(...numericPrices) : Number.NEGATIVE_INFINITY;

        if (numericPrediction.length > 0) {
          dayMin = Math.min(dayMin, Math.min(...numericPrediction));
          dayMax = Math.max(dayMax, Math.max(...numericPrediction));
        }

         let bounds: { min: number; max: number };
         if (dayMin >= this.priceDefaultYMin && dayMax <= this.priceDefaultYMax) {
           bounds = { min: this.priceDefaultYMin, max: this.priceDefaultYMax };
         } else {
           bounds = this.getPriceBoundsWithDefaults(dayMin, dayMax);
         }

            option.yAxis.push({ type: 'value', name: 'Preis (ct/kWh)', min: bounds.min, max: bounds.max, axisLabel: { color: '#000' } });
        option.series.push({
          name: 'Kundenpreis',
          type: 'line',
          data: fullPrices as any,
          smooth: false,
          showSymbol: false,
          yAxisIndex: 1,
          lineStyle: { width: 2, color: '#c23531' },
          itemStyle: { color: '#c23531' },
        });
      } else if (hasPredictionData && !hasRealData && !hasSimulationData) {
        // Add y-axis for prediction prices only if no real/simulation prices exist
        const numericPrediction = fullPredictionPrices.filter((p): p is number => p !== null && p !== undefined);
        if (numericPrediction.length > 0) {
          const dayMin = Math.min(...numericPrediction);
          const dayMax = Math.max(...numericPrediction);
           let bounds;
           if (dayMin >= this.priceDefaultYMin && dayMax <= this.priceDefaultYMax) {
             bounds = { min: this.priceDefaultYMin, max: this.priceDefaultYMax };
           } else {
             bounds = this.getPriceBoundsWithDefaults(dayMin, dayMax);
           }
           option.yAxis.push({ type: 'value', name: 'Preis (ct/kWh)', min: bounds.min, max: bounds.max, axisLabel: { color: '#000' } });
        }
      }

      // Add prediction prices if available (light blue line)
      if (hasPredictionData && fullPredictionPrices.length > 0 && fullPredictionPrices.some(p => p !== null && p !== undefined)) {
        if (!hasRealData && !hasSimulationData) {
          // If only prediction data, add the first y-axis if not already added
         if (option.yAxis.length === 1) {
           const numericPrediction = fullPredictionPrices.filter((p): p is number => p !== null && p !== undefined);
           if (numericPrediction.length > 0) {
             const dayMin = Math.min(...numericPrediction);
             const dayMax = Math.max(...numericPrediction);
             let bounds;
             if (dayMin >= this.priceDefaultYMin && dayMax <= this.priceDefaultYMax) {
               bounds = { min: this.priceDefaultYMin, max: this.priceDefaultYMax };
             } else {
               bounds = this.getPriceBoundsWithDefaults(dayMin, dayMax);
             }
              option.yAxis[option.yAxis.length - 1] = { type: 'value', name: 'Preis (ct/kWh)', min: bounds.min, max: bounds.max, axisLabel: { color: '#000' } };
           }
         }
       }

       option.series.push({
         name: 'Vorhersagepreis',
         type: 'line',
         data: fullPredictionPrices as any,
         smooth: false,
         showSymbol: false,
         yAxisIndex: option.yAxis.length - 1,
         lineStyle: { width: 2, color: '#87CEEB' },
         itemStyle: { color: '#87CEEB' },
       });
     }

     // Add prediction forecast (load) if available (turquoise bar chart, grouped with real loads)
     if (hasPredictionData && fullForecasts && fullForecasts.length > 0 && fullForecasts.some(f => f !== null && f !== undefined)) {
       const numericForecasts = fullForecasts.filter((f): f is number => f !== null && f !== undefined);
       if (numericForecasts.length > 0) {
         const forecastMin = Math.min(...numericForecasts);
         const forecastMax = Math.max(...numericForecasts);

         // Use the same y-axis as real loads (index 0)
         option.series.push({
           name: 'Vorhersage-Auslastung',
           type: 'bar',
           data: fullForecasts as any,
           itemStyle: {
             color: 'rgb(32 178 170 / 0.44)',
             opacity: 0.7,
           },
           yAxisIndex: 0,
         });
       }
     }

     // Use notMerge = true to ensure previous secondary axes/series are removed
     this.chartInstance?.setOption(option, true);
   }

  // navigation is handled globally via DateSelectionService / Datepicker

  private getPriceBoundsWithDefaults(dayMin: number, dayMax: number): { min: number; max: number } {
    if (dayMin === Number.POSITIVE_INFINITY || dayMax === Number.NEGATIVE_INFINITY) {
      return { min: this.priceDefaultYMin, max: this.priceDefaultYMax };
    }
    let minBound = dayMin < this.priceDefaultYMin ? dayMin : this.priceDefaultYMin;
    let maxBound = dayMax > this.priceDefaultYMax ? dayMax : this.priceDefaultYMax;
    if (minBound === maxBound) {
      const eps = Math.abs(minBound) * 1e-6 || 1e-3;
      minBound -= eps;
      maxBound += eps;
    }
    return { min: minBound, max: maxBound };
  }

  // AI mode removed - price curve shown by default

  private readonly onResize = () => {
    if (this.chartInstance) this.chartInstance.resize();
  };

  ngOnDestroy(): void {
    window.removeEventListener('resize', this.onResize);
    if (this.chartInstance) {
      this.chartInstance.dispose();
      this.chartInstance = null;
    }
    if (this.dateSub) this.dateSub.unsubscribe();
  }
}





